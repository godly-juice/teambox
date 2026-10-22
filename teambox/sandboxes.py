"""PRIVATE SANDBOXES: data/sandboxes/<member>/<name>/ = a git repo (with its working tree), seeded from the clean STARTER template
(teambox/starter/ - README, guardrail files, an example). Never any copy of BESNIK code: the starter is the only seed.
Every sandbox and the shared team project (data/team-main/) descend from the same single starter commit (data/starter.git), so a
promote is an ordinary git merge.
Server-side git always runs with hooks off, symlinks checked out as plain files, fsmonitor off, and the sandbox hides refs/teambox/*
(build results, checkpoints) from pushes, so a member can not rewrite what the admin reviews."""
import os
import re
import shutil
import subprocess
import threading
import time

from . import store as S

GIT = shutil.which("git") or "/usr/bin/git"
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.symlinks=false", "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=always",
        "-c", "user.name=Team box", "-c", "user.email=teambox@team.local", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
        "-c", "core.autocrlf=false", "-c", "safe.directory=*"]
STARTER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "starter")
DEFAULT_NAME = "my-sandbox"


class SandboxError(Exception):
    pass


def git(repo, *args, check=True, env=None, input_=None):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", HOME=os.environ.get("HOME", "/tmp"))
    e.pop("GIT_DIR", None)
    if env:
        e.update(env)
    r = subprocess.run([GIT] + SAFE + (["-C", repo] if repo else []) + list(args), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e, input=input_)
    if check and r.returncode != 0:
        raise SandboxError("git %s: %s" % (args[0], (r.stderr or r.stdout).strip()[:400]))
    return r.stdout.strip() if check else r


class Sandboxes:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.root = store.p("sandboxes")
        self.starter_git = store.p("starter.git")
        self.team_main = store.p("team-main")
        self.locks = {}
        self.busy = {}
        self._l = threading.Lock()
        os.makedirs(self.root, exist_ok=True)
        self.init_starter()

    # ------------------------------------------------------------ the starter commit + team-main
    def init_starter(self):
        if not os.path.isdir(self.starter_git):
            tmp = self.starter_git + ".build"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(STARTER_DIR, tmp)
            git(tmp, "init", "-q", "-b", "main")
            git(tmp, "add", "-A")
            git(tmp, "commit", "-q", "-m", "Team sandbox starter (clean template - no BESNIK code)")
            git(None, "clone", "-q", "--bare", tmp, self.starter_git)
            shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.isdir(os.path.join(self.team_main, ".git")):
            git(None, "clone", "-q", self.starter_git, self.team_main)
            git(self.team_main, "remote", "remove", "origin")
            self._harden(self.team_main, pushable=False)
        self.starter_sha = git(self.starter_git, "rev-parse", "main")

    def _harden(self, repo, pushable):
        hooks = self.store.p("empty-hooks")
        os.makedirs(hooks, exist_ok=True)
        for k, v in (("core.hooksPath", hooks), ("core.symlinks", "false"), ("core.fsmonitor", "false"), ("receive.fsckObjects", "true"),
                     ("receive.hideRefs", "refs/teambox"),("user.name", "Team box"), ("user.email", "teambox@team.local"),
                     ("gc.auto", "0")):
            git(repo, "config", k, v)
        if pushable:
            git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
            git(repo, "config", "http.receivepack", "true")
            git(repo, "config", "http.uploadpack", "true")
            git(repo, "config", "receive.denyDeleteCurrent", "true")
        hd = os.path.join(repo, ".git", "hooks")
        shutil.rmtree(hd, ignore_errors=True)                    # no sample hooks either

    # ------------------------------------------------------------ paths + access
    def path(self, slug, name):
        if not S.SLUG_RX.match(slug or "") or not S.NAME_RX.match(name or ""):
            raise SandboxError("no such sandbox")
        p = os.path.join(self.root, slug, name)
        if not os.path.isdir(os.path.join(p, ".git")):
            raise SandboxError("no such sandbox")
        return p

    def exists(self, slug, name):
        try:
            self.path(slug, name)
            return True
        except SandboxError:
            return False

    def lock(self, slug, name):
        with self._l:
            return self.locks.setdefault((slug, name), threading.Lock())

    # ------------------------------------------------------------ create / list
    @staticmethod
    def clean_name(name):
        n = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")[:40]
        if not n or not S.NAME_RX.match(n):
            raise SandboxError("name it with letters and numbers, e.g. 'weather-bot'")
        return n

    def create(self, slug, name, label=None):
        name = self.clean_name(name)
        mine = self.list(slug)
        if any(s["name"] == name for s in mine):
            raise SandboxError("you already have a sandbox called '%s'" % name)
        if len(mine) >= int(self.cfg["limits"]["sandboxes_per_member"]):
            raise SandboxError("you have %d sandboxes already (the most allowed)" % len(mine))
        dest = os.path.join(self.root, slug, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        git(None, "clone", "-q", self.starter_git, dest)
        git(dest, "remote", "remove", "origin")
        self._harden(dest, pushable=True)
        S.jwrite(os.path.join(self.root, slug, name + ".meta.json"), {"name": name, "label": (label or name)[:60], "created": S.now_s(),
                                                                       "owner": slug})
        return self.info(slug, name, light=True)

    def ensure_default(self, slug):
        if not self.list(slug):
            self.create(slug, DEFAULT_NAME, "My project")

    def list(self, slug):
        d = os.path.join(self.root, slug)
        out = []
        if S.SLUG_RX.match(slug or "") and os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if os.path.isdir(os.path.join(d, n, ".git")):
                    out.append(self.info(slug, n, light=True))
        return out

    def all(self):
        out = []
        for slug in sorted(os.listdir(self.root)):
            if os.path.isdir(os.path.join(self.root, slug)):
                out += self.list(slug)
        return out

    def size_mb(self, p):
        tot = 0
        for dp, _ds, fs in os.walk(p):
            for f in fs:
                try:
                    tot += os.lstat(os.path.join(dp, f)).st_size
                except OSError:
                    pass
        return round(tot / 2 ** 20, 2)

    def info(self, slug, name, light=False):
        p = self.path(slug, name)
        meta = S.jread(os.path.join(self.root, slug, name + ".meta.json"), {}) or {}
        out = {"owner": slug, "owner_email": self.store.email_for(slug), "name": name, "label": meta.get("label", name),
               "created": meta.get("created"), "busy": self.busy.get((slug, name)),
               "git_path": "/git/%s/%s.git" % (slug, name)}
        if not light:
            out["head"] = git(p, "rev-parse", "HEAD")
            out["files"] = [f for f in git(p, "ls-files").splitlines() if f][:2000]
            out["log"] = [ln.split("\x1f") for ln in git(p, "log", "-n", "25", "--format=%h\x1f%ad\x1f%an\x1f%s", "--date=format:%Y-%m-%d %H:%M").splitlines()]
            out["size_mb"] = self.size_mb(p)
        return out

    def read_file(self, slug, name, rel):
        """Read a committed file of the sandbox via git (so no symlink or path trick reaches outside it)."""
        p = self.path(slug, name)
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/") or rel.startswith(".git/") or rel == ".git":
            raise SandboxError("bad path")
        r = git(p, "show", "HEAD:" + rel, check=False)
        if r.returncode != 0:
            raise SandboxError("no such file")
        return r.stdout[:400000]

    def test_command(self, p):
        """The sandbox's own test command (teambox.json {"test": [...]}) - safe to take from the member: it only ever runs in the jail."""
        j = S.jread(os.path.join(p, "teambox.json"), {}) or {}
        t = j.get("test")
        if isinstance(t, list) and t and all(isinstance(x, str) for x in t) and len(t) < 40:
            return t
        return ["python3", "-m", "unittest", "discover", "-v"]

    # ------------------------------------------------------------ team-main
    def team_main_info(self):
        return {"head": git(self.team_main, "rev-parse", "HEAD"),
                "files": [f for f in git(self.team_main, "ls-files").splitlines() if f][:2000],
                "log": [ln.split("\x1f") for ln in git(self.team_main, "log", "-n", "40", "--format=%h\x1f%ad\x1f%an\x1f%s",
                                                        "--date=format:%Y-%m-%d %H:%M").splitlines()]}


def wait_until(fn, timeout=30, step=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(step)
    return False
