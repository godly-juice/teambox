"""AI BUILDS in a member's sandbox - the tool loop of BESNIK's lumi_builder.py, adapted for the team box:
 - the model is called through BESNIK's team gateway as THAT member (X-Team-Account), so the member's caps apply
 - the gateway passes no native tools, so tools are described in the system prompt and called as <tool_call>{json}</tool_call>
 - tools: list_dir / read_file / search / write_file / edit_file / run_test / checkpoints / rollback / finish
 - every ACCEPTED write (Python compiles, JSON loads) is a CHECKPOINT = a git commit + ref refs/teambox/cp/<build>/<n>
 - run_test runs in the JAIL (bubblewrap, no network, memory/time caps, only a copy of the sandbox writable)
 - TRIPWIRE: a test that contains a word on the never-in-a-test list is not run; the model is told the word, the line and why, and
   keeps working (it never waits). An admin can "Let it pass" that exact file content later.
 - one build per member at a time, plus a global limit (config builds.max_total)
The build works on a detached HEAD from the sandbox's main and ends at refs/teambox/builds/<id> (hidden from pushes). Its review
diff is computed in the team project (what a promote would add). Pushes from a member's own AI tool become "push" builds the same way."""
import hashlib
import json
import os
import re
import secrets
import shlex
import threading
import time
import traceback

from . import jail
from . import store as S
from .sandboxes import SandboxError, git

PROTECTED_FILES = {"CLAUDE.md", "AGENTS.md", ".cursorrules", "TEAM-SANDBOX-RULES.md", "opencode.json", "GEMINI.md",
                   ".cursor/rules/team-sandbox.mdc", ".github/copilot-instructions.md"}
MAX_WRITE = 300_000

# word/pattern -> why. Linux team box version of BESNIK's list: things a TEST never needs, that point at escaping or probing.
TRIPWIRE = [
    (r"/proc/(?:1|self|\d+)/(?:root|environ|mem|ns|cwd)", "reads another process's files, memory or namespaces"),
    (r"\bnsenter\b|\bunshare\b|\bsetns\b", "tries to switch namespaces (leave the jail)"),
    (r"\bptrace\b", "attaches to other programs"),
    (r"\bchroot\b|\bpivot_root\b|\bmount\s+-", "changes mounts (leave the jail)"),
    (r"/etc/shadow|/etc/sudoers|\bsudo\s|\bsu\s+-", "goes after system passwords or admin rights"),
    (r"docker\.sock|containerd\.sock|podman\.sock", "talks to a container engine"),
    (r"169\.254\.169\.254", "asks a cloud metadata service for keys"),
    (r"\bbwrap\b|bubblewrap|seccomp", "probes the jail itself"),
    (r"cloudflared|cf-access-|CF_Authorization", "goes after the tunnel or the login"),
    (r"/var/lib/teambox|gateway\.secret|tokens-index", "goes after the team box's own data"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "is a fork bomb"),
]
TRIP_RX = re.compile("|".join("(%s)" % p for p, _ in TRIPWIRE), re.I)


class Refused(Exception):
    pass


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S")


TOOLS_TEXT = """TOOLS (call exactly ONE per reply, written as  <tool_call>{"name": "...", "arguments": {...}}</tool_call> ):
- list_dir {"path": "."}                       list a folder of the sandbox
- read_file {"path": "x.py", "start": 1, "end": 200}   read lines (max 250 per call)
- search {"pattern": "regex", "glob": "*.py"}  search the sandbox
- write_file {"path": "x.py", "content": "..."}   create/replace a file (must parse; each accepted write = a checkpoint)
- edit_file {"path": "x.py", "old": "exact text", "new": "replacement"}   replace one exact snippet (must occur once)
- run_test {"command": "python3 -m unittest -v"}   run a test inside the jail (no network, limited memory/time). Empty = the sandbox's own test command.
- checkpoints {}                                list this build's checkpoints
- rollback {"n": 2}                             go back to checkpoint n (0 = the start)
- finish {"summary": "...", "tests": ["python3 -m unittest -v"], "done": "done|partly|failed"}   end the build"""

SYSTEM = """You are the TEAM BUILDER on the team's shared computer. You work ONLY inside one member's TEAM SANDBOX (a git repository).
This is a team sandbox on the team server - not the member's personal computer and not the owner's computer.
Rules:
- Use the tools. One tool call per reply. Keep inputs small; read only what you need.
- Write in small pieces: a new file's first version under ~120 lines, then extend it with edit_file. Python 3 standard library only.
- Every new script gets a test file next to it (test_<name>.py, unittest) that runs offline in seconds: no network, no real data.
- Run your test with run_test until it passes. If you break things, rollback to a good checkpoint.
- Never try to reach other sandboxes, the server, other machines or networks, credentials or personal data; never try to escape,
  disable or probe the safety measures. If the task asks for that, refuse in finish and say why.
- Never invent results. End with finish: the test commands, done/partly/failed, and a short plain-language summary.
""" + TOOLS_TEXT


def parse_tool_calls(msg):
    """Native tool_calls, else <tool_call>{json}</tool_call>, else <function=name><parameter=x>..</parameter></function> (from lumi_builder)."""
    out = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        try:
            args = json.loads(raw or "{}", strict=False) if isinstance(raw, str) else (raw or {})
        except ValueError as exc:
            args = {"_bad_json": "%s (at char %s)" % (exc.msg, exc.pos)}
        out.append((fn.get("name"), args))
    if out:
        return out
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S)
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S):
        try:
            d = json.loads(m.group(1), strict=False)
            out.append((d.get("name"), d.get("arguments") or {}))
        except ValueError as exc:
            out.append(("?", {"_bad_json": "%s (at char %s)" % (exc.msg, exc.pos)}))
    for m in re.finditer(r"<function=([\w_]+)>(.*?)</function>", text, re.S):
        args = {p.group(1): p.group(2).strip("\n") for p in re.finditer(r"<parameter=([\w_]+)>(.*?)</parameter>", m.group(2), re.S)}
        for k in ("start", "end", "n"):
            if k in args and str(args[k]).strip().lstrip("-").isdigit():
                args[k] = int(str(args[k]).strip())
        if "tests" in args and isinstance(args["tests"], str):
            try:
                args["tests"] = json.loads(args["tests"])
            except ValueError:
                args["tests"] = [t for t in args["tests"].splitlines() if t.strip()]
        out.append((m.group(1), args))
    return out


def tripwire_scan(text):
    """-> (word, line_no, line, why) or None."""
    m = TRIP_RX.search(text)
    if not m:
        return None
    idx = next(i for i, g in enumerate(m.groups()) if g is not None)
    line_no = text.count("\n", 0, m.start()) + 1
    lines = text.splitlines()
    line = lines[line_no - 1].strip()[:200] if lines else ""
    return m.group(0), line_no, line, TRIPWIRE[idx][1]


# ===================================================================== the manager
class Builds:
    def __init__(self, cfg, store, sandboxes, gateway):
        self.cfg, self.store, self.sb, self.gw = cfg, store, sandboxes, gateway
        self.dir = store.p("builds")
        self.tmp = store.p("tmp")
        os.makedirs(self.dir, exist_ok=True)
        self.running = {}                      # build id -> member slug
        self.l = threading.RLock()
        self.trip_f = store.p("tripwire.json")
        self.retry_sleep = 10
        self.recover()

    # ---- records
    def rec_path(self, bid):
        if not re.match(r"^[0-9a-z-]{8,80}$", bid or ""):
            raise SandboxError("no such build")
        return os.path.join(self.dir, bid + ".json")

    def get(self, bid):
        r = S.jread(self.rec_path(bid))
        if not r:
            raise SandboxError("no such build")
        return r

    def save(self, rec):
        rec["updated"] = now_s()
        with self.l:
            S.jwrite(self.rec_path(rec["id"]), rec)

    def update(self, bid, **kv):
        with self.l:
            r = self.get(bid)
            r.update(kv)
            self.save(r)
            return r

    def list(self, slug=None):
        out = []
        for f in sorted(os.listdir(self.dir), reverse=True):
            if f.endswith(".json"):
                r = S.jread(os.path.join(self.dir, f))
                if r and (slug is None or r.get("owner") == slug):
                    out.append({k: r.get(k) for k in ("id", "kind", "owner", "owner_email", "sandbox", "title", "status", "verdict",
                                                      "created", "updated", "model", "steps", "files_changed", "last")})
        return out

    def bdir(self, bid):
        d = os.path.join(self.dir, bid)
        os.makedirs(d, exist_ok=True)
        return d

    def recover(self):
        for b in self.list():
            if b["status"] in ("running", "queued", "testing"):
                self.update(b["id"], status="interrupted", last="the team box restarted while this was running")
                try:
                    p = self.sb.path(b["owner"], b["sandbox"])
                    git(p, "checkout", "-q", "-f", "main", check=False)
                    git(p, "clean", "-qfd", check=False)
                except SandboxError:
                    pass

    # ---- tripwire pending / approved (exact content sha)
    def trip(self):
        return S.jread(self.trip_f, {"pending": [], "approved": {}}) or {"pending": [], "approved": {}}

    def trip_check(self, rel, raw, bid, owner):
        text = raw.decode("utf8", "replace") if isinstance(raw, bytes) else raw
        hit = tripwire_scan(text)
        if not hit:
            return
        sha = hashlib.sha256(text.encode("utf8")).hexdigest()
        with self.l:
            t = self.trip()
            if sha in t["approved"]:
                return
            word, line_no, line, why = hit
            t["pending"] = [p for p in t["pending"] if p.get("sha") != sha][-99:]
            t["pending"].append({"sha": sha, "file": rel, "word": word, "line": line_no, "text": line, "why": why, "build": bid,
                                 "owner": owner, "at": now_s()})
            S.jwrite(self.trip_f, t)
        raise Refused("BLOCKED by the safety check: %s line %d contains '%s' (%s): %s . An admin can let this exact file pass, but do NOT "
                      "wait for that - keep working: write the test without that word, or finish without this test and say so."
                      % (rel, line_no, word, why, line))

    def letpass(self, sha, admin_email):
        with self.l:
            t = self.trip()
            hit = next((p for p in t["pending"] if p.get("sha") == sha), None)
            if not hit:
                return None
            t["approved"][sha] = dict(hit, approved=now_s(), by=admin_email)
            t["pending"] = [p for p in t["pending"] if p.get("sha") != sha]
            S.jwrite(self.trip_f, t)
            return hit

    # ---- start
    def start(self, slug, email, sandbox, task, model, settings=None):
        task = (task or "").strip()
        if not task:
            raise SandboxError("say what the AI should build")
        if not model:
            raise SandboxError("pick a model")
        p = self.sb.path(slug, sandbox)
        with self.l:
            mine = [b for b, o in self.running.items() if o == slug]
            if mine:
                raise SandboxError("you already have a build running (%s) - one at a time" % mine[0])
            if len(self.running) >= int(self.cfg["builds"]["max_total"]):
                raise SandboxError("the team box is running %d builds already (the most at once) - try again in a few minutes" % len(self.running))
            lk = self.sb.lock(slug, sandbox)
            if not lk.acquire(blocking=False):
                raise SandboxError("this sandbox is busy (%s)" % (self.sb.busy.get((slug, sandbox)) or "a push or build"))
            bid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            self.running[bid] = slug
        self.sb.busy[(slug, sandbox)] = "AI build %s" % bid
        title = re.sub(r"\s+", " ", task)[:80]
        rec = {"id": bid, "kind": "ai", "owner": slug, "owner_email": email, "sandbox": sandbox, "title": title, "task": task[:8000],
               "model": model, "settings": settings or {}, "status": "running", "verdict": None, "created": now_s(), "steps": 0,
               "checkpoints": [], "last": "starting", "actions": []}
        self.save(rec)
        th = threading.Thread(target=self._run_thread, args=(rec, p, lk), daemon=True, name="build-" + bid)
        th.start()
        return rec

    def _run_thread(self, rec, p, lk):
        try:
            Run(self, rec, p).go()
        except Exception as exc:  # noqa: BLE001
            self.update(rec["id"], status="error", last="the build crashed: %r" % exc, trace=traceback.format_exc()[-3000:])
        finally:
            try:
                git(p, "checkout", "-q", "-f", "main", check=False)
                git(p, "clean", "-qfd", check=False)
            finally:
                self.sb.busy.pop((rec["owner"], rec["sandbox"]), None)
                with self.l:
                    self.running.pop(rec["id"], None)
                lk.release()

    def stop(self, bid):
        open(os.path.join(self.bdir(bid), "STOP"), "w").close()

    # ---- after a push from the member's own AI tool
    def record_push(self, slug, email, sandbox, old, new, lk_held=None):
        bid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        p = self.sb.path(slug, sandbox)
        msg = git(p, "log", "-1", "--format=%s", new, check=False).stdout.strip()
        rec = {"id": bid, "kind": "push", "owner": slug, "owner_email": email, "sandbox": sandbox, "title": "push: " + (msg or new[:10])[:70],
               "task": "pushed from the member's own AI tool (%s..%s)" % ((old or "start")[:10], new[:10]), "status": "testing", "created": now_s(),
               "steps": 0, "checkpoints": [], "last": "running the sandbox's tests in the jail", "actions": [], "head": new}
        self.save(rec)
        git(p, "update-ref", "refs/teambox/builds/" + bid, new)
        th = threading.Thread(target=self._push_thread, args=(rec, p), daemon=True)
        th.start()
        return rec

    def _push_thread(self, rec, p):
        lk = self.sb.lock(rec["owner"], rec["sandbox"])
        with lk:
            try:
                tests = [self.jail_test(p, self.sb.test_command(p), rec["id"])]
                flags = []
                for f in git(p, "ls-files").splitlines():
                    full = os.path.join(p, f)
                    if os.path.isfile(full) and not os.path.islink(full) and os.path.getsize(full) < MAX_WRITE:
                        with open(full, encoding="utf8", errors="replace") as fh:
                            hit = tripwire_scan(fh.read())
                        if hit:
                            flags.append("%s line %d contains '%s' (%s)" % (f, hit[1], hit[0], hit[3]))
                self.finalize(rec["id"], p, rec["head"], tests, None, flags)
            except Exception as exc:  # noqa: BLE001
                self.update(rec["id"], status="error", last="testing the push failed: %r" % exc)

    # ---- tests in the jail
    def jail_test(self, p, argv, bid):
        try:
            r = jail.run(self.cfg["jail"], p, argv, self.tmp, int(self.cfg["limits"]["sandbox_max_mb"]) * 2 ** 20)
        except jail.JailError as exc:
            return {"command": " ".join(argv), "ok": False, "exit": None, "output": "NOT RUN - no jail: %s" % exc}
        return {"command": " ".join(argv), "ok": r["ok"], "exit": r["exit"], "output": r["output"][-6000:], "seconds": r["seconds"]}

    # ---- review material: what a promote would add to the team project
    def finalize(self, bid, p, head, tests, fin, flags):
        tm = self.sb.team_main
        ref = "refs/review/" + bid
        with self.l:
            git(tm, "fetch", "-q", "--no-tags", p, "+refs/teambox/builds/%s:%s" % (bid, ref))
            mb = git(tm, "merge-base", "HEAD", ref, check=False).stdout.strip() or self.sb.starter_sha
            diff = git(tm, "diff", mb, ref)
            names = [ln.split("\t", 1) for ln in git(tm, "diff", "--name-status", mb, ref).splitlines() if "\t" in ln]
        d = self.bdir(bid)
        with open(os.path.join(d, "diff.patch"), "w", encoding="utf8") as fh:
            fh.write(diff + "\n")
        with open(os.path.join(d, "tests.txt"), "w", encoding="utf8") as fh:
            for t in tests:
                fh.write("$ %s\n-> %s (exit %s)\n%s\n\n" % (t["command"], "PASS" if t["ok"] else "FAIL", t["exit"], t["output"]))
        files = [{"status": s[0], "path": f} for s, f in names]
        ok = bool(tests) and all(t["ok"] for t in tests)
        verdict = ("tests pass" if ok else "tests FAIL") if files else "no change to the team project"
        summary = ((fin or {}).get("summary") or "").strip()
        for f in files:
            if f["path"] in PROTECTED_FILES:
                flags.append("changes a guardrail file: %s" % f["path"])
        self.update(bid, status="waiting for review" if files else "nothing to promote", verdict=verdict, head=head, review_base=mb,
                    files=files, files_changed=len(files), tests=[{k: t.get(k) for k in ("command", "ok", "exit")} for t in tests],
                    summary=summary or "(no summary)", model_says=(fin or {}).get("done"), flags=flags,
                    last="finished: %s" % verdict)

    def review(self, bid):
        r = self.get(bid)
        d = os.path.join(self.dir, bid)
        out = dict(r)
        for k, f in (("diff", "diff.patch"), ("tests_text", "tests.txt")):
            try:
                out[k] = open(os.path.join(d, f), encoding="utf8", errors="replace").read()[:300000]
            except OSError:
                out[k] = ""
        try:
            out["transcript"] = [json.loads(ln) for ln in open(os.path.join(d, "transcript.jsonl"), encoding="utf8")][-80:]
        except (OSError, ValueError):
            out["transcript"] = []
        return out

    # ---- decisions
    def reject(self, bid, actor_email, actor_admin, actor_slug):
        with self.l:
            r = self.get(bid)
            if not actor_admin and r["owner"] != actor_slug:
                raise PermissionError("members can reject only their own builds")
            if r["status"] in ("promoted", "rejected"):
                raise SandboxError("this build is already %s" % r["status"])
            if r["status"] in ("running", "testing"):
                raise SandboxError("stop the build first")
            r["status"] = "rejected"
            r.setdefault("actions", []).append({"at": now_s(), "by": actor_email, "action": "reject"})
            self.save(r)
            return r

    def promote(self, bid, admin_email, head):
        """Admin only (the caller checks the role too - this is the second check). Merges the build into the shared team project."""
        tm = self.sb.team_main
        with self.l:
            r = self.get(bid)
            if r["status"] != "waiting for review":
                raise SandboxError("only a build that is 'waiting for review' can be promoted (this one: %s)" % r["status"])
            if not head or head != r.get("head"):
                raise SandboxError("confirm the exact build you reviewed (its head commit) - it does not match")
            p = self.sb.path(r["owner"], r["sandbox"])
            ref = "refs/review/" + bid
            git(tm, "fetch", "-q", "--no-tags", p, "+refs/teambox/builds/%s:%s" % (bid, ref))
            got = git(tm, "rev-parse", ref)
            if got != head:
                raise SandboxError("the build changed since review (%s != %s)" % (got[:10], head[:10]))
            res = git(tm, "merge", "--no-ff", "--no-edit", "-m", "Promote %s from %s/%s: %s (by %s)" % (bid, r["owner"], r["sandbox"], r["title"], admin_email),
                      ref, check=False)
            if res.returncode != 0:
                conflicts = git(tm, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
                git(tm, "merge", "--abort", check=False)
                git(tm, "reset", "-q", "--hard", "HEAD", check=False)
                raise SandboxError("it does not merge cleanly into the team project (conflicts: %s) - ask the member to update their sandbox"
                                   % (", ".join(conflicts) or (res.stderr or res.stdout).strip()[:200]))
            commit = git(tm, "rev-parse", "HEAD")
            r["status"] = "promoted"
            r["promoted_commit"] = commit
            r.setdefault("actions", []).append({"at": now_s(), "by": admin_email, "action": "promote", "commit": commit})
            self.save(r)
        t = self.jail_test(tm, self.sb.test_command(tm), bid)
        self.update(bid, team_main_tests={"ok": t["ok"], "exit": t["exit"], "output": t["output"][-3000:]})
        return self.get(bid)

    def apply(self, bid, slug):
        """The owner takes an AI build's result into their sandbox's main (so their clone gets it on the next pull)."""
        r = self.get(bid)
        if r["owner"] != slug:
            raise PermissionError("only the owner can apply a build to their sandbox")
        if r["kind"] != "ai" or r["status"] in ("running", "testing"):
            raise SandboxError("nothing to apply")
        p = self.sb.path(r["owner"], r["sandbox"])
        lk = self.sb.lock(r["owner"], r["sandbox"])
        if not lk.acquire(blocking=False):
            raise SandboxError("this sandbox is busy")
        try:
            res = git(p, "merge", "--no-edit", "-m", "Apply build %s" % bid, "refs/teambox/builds/" + bid, check=False)
            if res.returncode != 0:
                git(p, "merge", "--abort", check=False)
                raise SandboxError("it does not merge into your sandbox cleanly")
            r.setdefault("actions", []).append({"at": now_s(), "by": slug, "action": "apply"})
            r["applied"] = True
            self.save(r)
            return r
        finally:
            lk.release()


# ===================================================================== one AI build
class Run:
    def __init__(self, mgr, rec, path):
        self.m, self.rec, self.p = mgr, rec, path
        self.id = rec["id"]
        self.dir = mgr.bdir(self.id)
        self.cps = []
        self.t0 = time.time()
        self.steps = 0
        bc = mgr.cfg["builds"]
        self.max_steps, self.max_minutes, self.max_tokens = int(bc["max_steps"]), float(bc["max_minutes"]), int(bc["max_tokens"])
        self.budget_chars = 30000

    def tr(self, kind, **kv):
        kv.update(kind=kind, at=now_s())
        with open(os.path.join(self.dir, "transcript.jsonl"), "a", encoding="utf8") as fh:
            fh.write(json.dumps(kv, ensure_ascii=False) + "\n")

    def say(self, msg):
        self.m.update(self.id, last=msg[:400], steps=self.steps, checkpoints=self.cps)

    # ---- paths
    def guard(self, rel, write=False):
        rel = (rel or "").strip().strip('"').replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel in ("", "."):
            return "", self.p
        if rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel) or ".." in rel.split("/"):
            raise Refused("paths are relative to the sandbox, like app/tool.py")
        if rel.split("/")[0] == ".git":
            raise Refused("the .git folder is off limits")
        full = os.path.normpath(os.path.join(self.p, rel))
        if os.path.commonpath([os.path.realpath(full), os.path.realpath(self.p)]) != os.path.realpath(self.p):
            raise Refused("that path leaves the sandbox")
        q = self.p
        for part in rel.split("/"):
            q = os.path.join(q, part)
            if os.path.islink(q):
                raise Refused("links are not allowed")
        if write and rel in PROTECTED_FILES:
            raise Refused("%s holds the sandbox guardrails - the AI builder may not change it" % rel)
        return rel, full

    def checkpoint(self, what):
        git(self.p, "add", "-A")
        if not git(self.p, "status", "--porcelain", check=False).stdout.strip():
            return None
        n = len(self.cps) + 1
        git(self.p, "commit", "-q", "-m", "checkpoint %d: %s" % (n, what[:200]), "--author", "Team builder for %s <builder@team.local>" % self.rec["owner"])
        sha = git(self.p, "rev-parse", "HEAD")
        git(self.p, "update-ref", "refs/teambox/cp/%s/%d" % (self.id, n), sha)
        self.cps.append({"n": n, "what": what[:200], "at": now_s(), "commit": sha})
        return n

    def changed_files(self):
        return [f for f in git(self.p, "diff", "--name-only", self.base, "HEAD").splitlines() if f]

    def run_test(self, command):
        command = (command or "").strip()
        argv = shlex.split(command) if command else self.m.sb.test_command(self.p)
        if len(argv) > 40:
            raise Refused("test command too long")
        self.m.trip_check("(the test command)", " ".join(argv), self.id, self.rec["owner"])
        for f in self.changed_files():
            full = os.path.join(self.p, f)
            if os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as fh:
                    self.m.trip_check(f, fh.read(), self.id, self.rec["owner"])
        return self.m.jail_test(self.p, argv, self.id)

    # ---- tools
    def tool(self, name, a):
        if a.get("_bad_json"):
            raise Refused("your tool call was not valid JSON (%s); nothing was done. Send it again - for a big file write a short first "
                          "version, then edit_file." % a["_bad_json"])
        if name == "list_dir":
            _r, full = self.guard(a.get("path") or ".")
            if not os.path.isdir(full):
                return "not a folder"
            items = sorted(i for i in os.listdir(full) if i not in (".git", "__pycache__"))
            return "\n".join((i + "/") if os.path.isdir(os.path.join(full, i)) else "%s  (%d bytes)" % (i, os.path.getsize(os.path.join(full, i)))
                             for i in items)[:6000] or "(empty)"
        if name == "read_file":
            rel, full = self.guard(a.get("path"))
            if not os.path.isfile(full):
                return "no such file"
            lines = open(full, encoding="utf8", errors="replace").read().splitlines()
            s = max(1, int(a.get("start") or 1))
            e = min(len(lines), int(a.get("end") or s + 249), s + 249)
            return "%s lines %d-%d of %d\n%s" % (rel, s, e, len(lines), "\n".join("%d: %s" % (i, lines[i - 1][:400]) for i in range(s, e + 1)))
        if name == "search":
            try:
                rx = re.compile(a.get("pattern") or "", re.I)
            except re.error as exc:
                raise Refused("bad regex: %s" % exc)
            import fnmatch
            glob = a.get("glob") or "*"
            hits = []
            for f in git(self.p, "ls-files").splitlines():
                if not (fnmatch.fnmatch(f, glob) or fnmatch.fnmatch(os.path.basename(f), glob)):
                    continue
                full = os.path.join(self.p, f)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                for i, ln in enumerate(open(full, encoding="utf8", errors="replace"), 1):
                    if rx.search(ln):
                        hits.append("%s:%d: %s" % (f, i, ln.strip()[:200]))
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n(first 60 hits)"
            return "\n".join(hits) or "no match"
        if name in ("write_file", "edit_file"):
            rel, full = self.guard(a.get("path"), write=True)
            if not rel:
                raise Refused("give a file path")
            before = open(full, "rb").read() if os.path.exists(full) else None
            if name == "write_file":
                new = a.get("content")
                if not isinstance(new, str):
                    return "REFUSED: content must be text"
            else:
                if before is None:
                    return "REFUSED: no such file (use write_file to create it)"
                old_txt = before.decode("utf8", "replace")
                o, nw = a.get("old") or "\0\0", a.get("new") or ""
                cnt = old_txt.count(o)
                if cnt == 0 and re.search(r"(?m)^\d+: ", o):
                    o2 = re.sub(r"(?m)^\d+: ", "", o)
                    if old_txt.count(o2) == 1:
                        o, nw, cnt = o2, re.sub(r"(?m)^\d+: ", "", nw), 1
                if cnt != 1:
                    return "REFUSED: 'old' occurs %d times in %s (must be exactly once - copy it exactly, without line numbers)" % (cnt, rel)
                new = old_txt.replace(o, nw, 1)
            if len(new.encode("utf8")) > MAX_WRITE:
                return "REFUSED: file would be too big"
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf8", newline="") as fh:
                fh.write(new)
            ok, why = parses(rel, new)
            if not ok:
                if before is None:
                    os.remove(full)
                else:
                    with open(full, "wb") as fh:
                        fh.write(before)
                self.tr("rejected", tool=name, path=rel, why=why)
                return "NOT ACCEPTED (undone): %s" % why
            n = self.checkpoint("%s %s" % (name, rel))
            return ("accepted - checkpoint %s (%s, %d lines)" % (n, rel, new.count("\n") + 1)) if n else "no change"
        if name == "run_test":
            t = self.run_test(a.get("command"))
            self.tr("test", command=t["command"], ok=t["ok"], exit=t["exit"], out=t["output"][-3000:])
            return "%s (exit %s)\n%s" % ("PASSED" if t["ok"] else "FAILED", t["exit"], t["output"][-3500:])
        if name == "checkpoints":
            return "\n".join("%d  %s  %s" % (c["n"], c["at"], c["what"]) for c in self.cps) or "no checkpoints yet (0 = the start)"
        if name == "rollback":
            n = int(a.get("n", 0))
            if n < 0 or n > len(self.cps):
                return "no checkpoint %d" % n
            target = self.base if n == 0 else self.cps[n - 1]["commit"]
            git(self.p, "reset", "-q", "--hard", target)
            git(self.p, "clean", "-qfd")
            dropped = self.cps[n:]
            self.cps = self.cps[:n]
            self.tr("rollback", to=n, dropped=[c["n"] for c in dropped])
            return "rolled back to checkpoint %d (dropped %d later checkpoint(s))" % (n, len(dropped))
        raise Refused("unknown tool %s" % name)

    def trim(self, messages):
        total = sum(len(m["content"]) for m in messages)
        i = 2
        while total > self.budget_chars and i < len(messages) - 4:
            m = messages[i]
            if len(m["content"]) > 300:
                total -= len(m["content"]) - 80
                m["content"] = m["content"][:60] + " ...[older text trimmed - read it again if you need it]"
            i += 1
        return messages

    def model_call(self, messages):
        st = self.rec.get("settings") or {}
        body = {"model": self.rec["model"], "messages": self.trim(messages), "max_tokens": int(st.get("max_tokens") or self.max_tokens),
                "temperature": st.get("temperature", 0.3) if st.get("temperature") is not None else 0.3}
        if st.get("top_p") is not None:
            body["top_p"] = st["top_p"]
        status, j, why = self.m.gw.call("POST", "/v1/chat/completions", self.rec["owner_email"], body)
        if status == 413:
            self.budget_chars = max(6000, self.budget_chars // 2)
            raise RuntimeError("retry: the conversation was too long for the model - trimmed")
        if status in (401, 403, 409, 429) or (400 <= status < 500):
            raise PermissionError(why or "the team gateway refused (HTTP %s)" % status)
        if status != 200 or not j or "choices" not in j:
            raise RuntimeError(why or "the model gave no answer (HTTP %s)" % status)
        msg = j["choices"][0].get("message") or {}
        msg["_finish"] = j["choices"][0].get("finish_reason")
        return msg

    def go(self):
        p = self.p
        git(p, "checkout", "-q", "-f", "main")
        self.base = git(p, "rev-parse", "HEAD")
        git(p, "checkout", "-q", "--detach", self.base)
        self.tr("start", task=self.rec["task"], model=self.rec["model"], base=self.base)
        sys_prompt = SYSTEM
        if (self.rec.get("settings") or {}).get("system_prompt"):
            sys_prompt += "\nThe member's own model settings add:\n" + str(self.rec["settings"]["system_prompt"])[:2000]
        messages = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": "TASK (%s):\n%s\n\nThe sandbox's files:\n%s" % (self.rec["title"], self.rec["task"], self.tool("list_dir", {}))}]
        fin, nudges, errors, ended = None, 0, 0, None
        while self.steps < self.max_steps and (time.time() - self.t0) / 60 < self.max_minutes:
            if os.path.exists(os.path.join(self.dir, "STOP")):
                ended = "stopped by its owner"
                break
            self.steps += 1
            self.say("step %d: asking the model" % self.steps)
            t1 = time.time()
            try:
                msg = self.model_call(messages)
                errors = 0
            except PermissionError as exc:
                ended = "the team gateway refused: %s" % exc
                self.tr("refused", why=str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self.tr("model_error", error=str(exc)[:500])
                self.steps -= 1
                if errors >= 3:
                    ended = "the model did not answer 3 times: %s" % exc
                    break
                self.say("model problem (%s) - retrying" % str(exc)[:200])
                time.sleep(self.m.retry_sleep if not str(exc).startswith("retry:") else 0)
                continue
            calls = parse_tool_calls(msg)
            content = msg.get("content") or ""
            self.tr("model", content=content[:3000], calls=[[c[0], c[1]] for c in calls], seconds=round(time.time() - t1, 1))
            if not calls:
                nudges += 1
                messages.append({"role": "assistant", "content": content[:4000] or "(empty)"})
                if nudges >= 3:
                    ended = "the model stopped using tools 3 times"
                    break
                messages.append({"role": "user", "content": "Use one tool call now, as <tool_call>{\"name\": ..., \"arguments\": {...}}</tool_call> (or finish)."})
                continue
            nudges = 0
            name, args = calls[0]
            messages.append({"role": "assistant", "content": content[:8000] or "<tool_call>%s</tool_call>" % json.dumps({"name": name, "arguments": args})})
            if name == "finish":
                fin = args if isinstance(args, dict) else {}
                break
            try:
                if msg.get("_finish") == "length" and name in ("write_file", "edit_file"):
                    raise Refused("your answer was CUT OFF at the length limit, so nothing was written. Write a much shorter first version, "
                                  "then add to it with edit_file.")
                res = self.tool(name, args if isinstance(args, dict) else {})
            except Refused as exc:
                res = "REFUSED: %s" % exc
            except SandboxError as exc:
                res = "ERROR: %s" % exc
            self.tr("tool", name=name, args={k: (v if len(str(v)) < 400 else str(v)[:400] + "...") for k, v in (args or {}).items()}, result=res[:1500])
            self.say("%s -> %s" % (name, res.splitlines()[0][:150] if res else ""))
            messages.append({"role": "user", "content": "RESULT of %s:\n%s" % (name, res[:7000])})
        else:
            ended = "hit the step/time limit"
        # ---- end: keep the result at a hidden ref, run the sandbox's tests in the jail, build the review
        self.m.update(self.id, status="testing", last="final tests in the jail" + (" (%s)" % ended if ended else ""), steps=self.steps,
                      checkpoints=self.cps, ended=ended)
        head = git(p, "rev-parse", "HEAD")
        git(p, "update-ref", "refs/teambox/builds/" + self.id, head)
        cmds = [c for c in ((fin or {}).get("tests") or []) if isinstance(c, str) and c.strip()][:4]
        tests = []
        for c in cmds:
            try:
                tests.append(self.run_test(c))
            except Refused as exc:
                tests.append({"command": c, "ok": False, "exit": None, "output": "NOT RUN: %s" % exc})
        default = self.m.sb.test_command(p)
        if " ".join(default) not in [t["command"] for t in tests]:
            tests.append(self.m.jail_test(p, default, self.id))
        flags = ["the build ended early: %s" % ended] if ended else []
        if head == self.base:
            flags.append("the AI made no change")
        self.m.finalize(self.id, p, head, tests, fin, flags)


def parses(rel, text):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".py":
        try:
            compile(text, rel, "exec")
        except SyntaxError as exc:
            return False, "Python syntax error line %s: %s" % (exc.lineno, exc.msg)
    elif ext == ".json":
        try:
            json.loads(text)
        except ValueError as exc:
            return False, "JSON error: %s" % exc
    return True, ""
