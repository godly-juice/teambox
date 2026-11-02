"""Team box test suite. Run on Linux (needs git + bubblewrap + prlimit):   python3 -m unittest -v tests.test_teambox
Starts: the REAL team box server (in-process, temp data dir, random port), a fake team gateway (tests/fake_gateway.py) and the BESNIK
fake llama-server (tests/fake_llama.py) as its chat upstream. Never calls a real model."""
import base64
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fake_gateway import FakeGateway  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox import identity, jail, tour  # noqa: E402
from teambox.modules import messages  # noqa: E402
from teambox.server import make_server  # noqa: E402

A, B, ADMIN = "alice@test.example", "bob@test.example", "marjus@test.example"
EXACT_BOTTOM = ("if you're still confused or concerned and want to verify something, click a link above or if you want a third party "
                "verification copy this text and paste it in your trusted Ai which will tell it to test our claims so you can have a clear "
                "explanation of what's here and how exactly it works.")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------- tiny RSA (tests only) for the Access JWT check
def _is_prime(n, rnd):
    if n < 4:
        return n in (2, 3)
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(20):
        a = rnd.randrange(2, n - 2)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def rsa_key(bits=1024, seed=7):
    rnd = random.Random(seed)
    e = 65537

    def prime():
        while True:
            c = rnd.getrandbits(bits // 2) | (1 << (bits // 2 - 1)) | 1
            if _is_prime(c, rnd) and (c - 1) % e:
                return c
    p, q = prime(), prime()
    n = p * q
    d = pow(e, -1, (p - 1) * (q - 1))
    return n, e, d


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_jwt(n, d, claims, kid="k1"):
    h = b64u(json.dumps({"alg": "RS256", "kid": kid}).encode())
    p = b64u(json.dumps(claims).encode())
    k = (n.bit_length() + 7) // 8
    t = identity.DIGESTINFO_SHA256 + hashlib.sha256((h + "." + p).encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return h + "." + p + "." + b64u(sig)


class TeamBoxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="teambox-test-")
        cls.secret = "test-secret-" + os.urandom(8).hex()
        sf = os.path.join(cls.tmp, "gateway.secret")
        with open(sf, "w") as fh:
            fh.write(cls.secret)
        cls.llama_port = free_port()
        cls.llama = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_llama.py"), "--port", str(cls.llama_port), "--tps", "50"])
        cls.gw = FakeGateway(cls.secret, [A, B, ADMIN], cls.llama_port)
        cls.port = free_port()
        cfgp = os.path.join(cls.tmp, "config.json")
        with open(cfgp, "w") as fh:
            json.dump({"port": cls.port, "data_dir": os.path.join(cls.tmp, "data"), "public_url": "http://127.0.0.1:%d" % cls.port,
                       "admin_emails": [ADMIN], "dev_mode": True,
                       "gateway": {"url": "http://127.0.0.1:%d" % cls.gw.port, "secret_file": sf, "timeout_s": 30},
                       "builds": {"max_total": 2, "max_steps": 12, "max_minutes": 5},
                       "jail": {"memory_mb": 512, "timeout_s": 20}}, fh)
        os.environ.pop("TEAMBOX_ALLOW_DEV", None)
        try:
            C.load(cfgp)
            raise AssertionError("dev_mode must refuse to load without TEAMBOX_ALLOW_DEV=1")
        except C.ConfigError:
            pass
        os.environ["TEAMBOX_ALLOW_DEV"] = "1"
        cls.cfg = C.load(cfgp)
        cls.app, cls.srv = make_server(cls.cfg)
        cls.app.builds.retry_sleep = 0.2
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/healthz", timeout=1)
                urllib.request.urlopen("http://127.0.0.1:%d/health" % cls.llama_port, timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        cls.gitenv = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", HOME=cls.tmp,
                          GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.gw.stop()
        cls.llama.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def api(self, method, path, who=None, body=None, headers=None, csrf=True):
        h = dict(headers or {})
        if who:
            h["X-Dev-Email"] = who
        if method == "POST" and csrf:
            h["X-Teambox"] = "1"
        data = None
        if body is not None or method == "POST":
            data = json.dumps(body or {}).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode()

    def me(self, who):
        return self.api("GET", "/api/me", who)[1]

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "credential.helper=", "-c", "init.defaultBranch=main"] + list(args), cwd=cwd, capture_output=True,
                              text=True, env=self.gitenv, timeout=120)

    def url(self, slug, name, token=None):
        auth = ("x:%s@" % token) if token else ""
        return "http://%s127.0.0.1:%d/git/%s/%s.git" % (auth, self.port, slug, name)

    def token(self, who, label="t"):
        s, j = self.api("POST", "/api/tokens", who, {"label": label})
        self.assertEqual(s, 200, j)
        return j["id"], j["token"]

    def wait_build(self, who, bid, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s, b = self.api("GET", "/api/builds/" + bid, who)
            if b.get("status") not in ("running", "testing", "queued"):
                return b
            time.sleep(0.3)
        self.fail("build %s did not finish: %s" % (bid, b))

    def start_build(self, who, task, sandbox="my-sandbox"):
        s, j = self.api("POST", "/api/builds", who, {"sandbox": sandbox, "task": task, "model": "builder-bot"})
        self.assertEqual(s, 200, j)
        return j["build"]["id"]

    # ============================================================ 1. identity
    def test_01_identity_and_trust(self):
        s, j = self.api("GET", "/api/me")
        self.assertEqual(s, 401, "no identity -> 401")
        self.app.cfg["dev_mode"] = False
        try:
            s, j = self.api("GET", "/api/me", headers={"Cf-Access-Authenticated-User-Email": ADMIN})
            self.assertEqual(s, 401, "the Cloudflare Access email header is NOT an identity any more (2026-10-21): %s" % j)
            s, j = self.api("GET", "/api/me", headers={"X-Dev-Email": A})
            self.assertEqual(s, 401, "dev header ignored when dev_mode is off")
            s, j = self.api("GET", "/api/me", headers={"Cookie": "tb_session=made-up-session-token"})
            self.assertEqual(s, 401, "a made-up session cookie gives nothing")
            self.app.cfg["trusted_proxy_ips"] = ["10.9.9.9"]
            s, j = self.api("GET", "/api/me", headers={"X-Dev-Email": ADMIN})
            self.assertEqual(s, 401, "requests not from the local cloudflared are refused: %s" % j)
        finally:
            self.app.cfg["trusted_proxy_ips"] = ["127.0.0.1", "::1"]
            self.app.cfg["dev_mode"] = True
        print("\n  identity: no session 401; Access email header alone 401; fake session 401; dev header ignored outside dev mode")

    def test_02_access_jwt(self):
        n, e, d = rsa_key()
        certs = os.path.join(self.tmp, "certs.json")
        with open(certs, "w") as fh:
            json.dump({"keys": [{"kty": "RSA", "kid": "k1", "n": b64u(n.to_bytes((n.bit_length() + 7) // 8, "big")), "e": b64u(e.to_bytes(3, "big"))}]}, fh)
        acc = {"team_domain": "team.example.cloudflareaccess.com", "aud": "aud123", "require_jwt": True, "certs_file": certs}
        identity._CERTS.update(t=0, keys={})
        good = {"email": "dana@test.example", "aud": ["aud123"], "iss": "https://team.example.cloudflareaccess.com", "exp": time.time() + 600}
        self.assertEqual(identity.verify_access_jwt(make_jwt(n, d, good), acc), ("dana@test.example", None))
        bad_sig = make_jwt(n, d, good)[:-6] + "AAAAAA"
        self.assertIsNone(identity.verify_access_jwt(bad_sig, acc)[0], "forged signature refused")
        self.assertIsNone(identity.verify_access_jwt(make_jwt(n, d, dict(good, aud=["other"])), acc)[0], "other app refused")
        self.assertIsNone(identity.verify_access_jwt(make_jwt(n, d, dict(good, exp=time.time() - 999)), acc)[0], "expired refused")
        self.app.cfg["access"] = dict(acc, enabled=False)
        self.assertEqual(self.api("GET", "/api/me", A)[0], 200, "outer lock OFF (the default): no Access token needed")
        self.app.cfg["access"] = dict(acc, enabled=True)
        try:
            s, j = self.api("GET", "/api/me", A, headers={"Cf-Access-Authenticated-User-Email": A})
            self.assertEqual(s, 401, "outer lock ON: a bare (forgeable) email header gives nothing")
            s, j = self.api("GET", "/api/me", A, headers={"Cf-Access-Jwt-Assertion": make_jwt(n, d, good)})
            self.assertEqual((s, j.get("email")), (200, A), "outer lock ON + valid Access token: identity still from the app's own sign-in")
            s, j = self.api("GET", "/api/me", A, headers={"Cf-Access-Jwt-Assertion": bad_sig})
            self.assertEqual(s, 401, "forged Access token refused by the outer lock")
        finally:
            self.app.cfg["access"] = dict(C.DEFAULTS["access"])
        print("\n  Access JWT: valid RS256 accepted; forged signature / other app / expired refused; outer lock off by default, on = token required, identity unchanged")

    # ============================================================ 2. sandboxes + isolation
    def test_10_default_sandbox_is_clean_starter(self):
        me = self.me(A)
        s, j = self.api("GET", "/api/sandboxes", A)
        self.assertEqual([x["name"] for x in j["sandboxes"]], ["my-sandbox"])
        s, info = self.api("GET", "/api/sandboxes/%s/my-sandbox" % me["id"], A)
        files = set(info["files"])
        for f in ("README.md", "CLAUDE.md", "AGENTS.md", ".cursorrules", "opencode.json", "TEAM-SANDBOX-RULES.md", "GEMINI.md",
                  ".cursor/rules/team-sandbox.mdc", ".github/copilot-instructions.md", "hello.py", "test_hello.py", "teambox.json"):
            self.assertIn(f, files)
        starter = set()
        for dp, ds, fs in os.walk(os.path.join(ROOT, "starter")):
            for f in fs:
                starter.add(os.path.relpath(os.path.join(dp, f), os.path.join(ROOT, "starter")).replace(os.sep, "/"))
        self.assertEqual(files, starter, "a new sandbox holds exactly the starter template - nothing else")
        rules = self.api("GET", "/api/sandboxes/%s/my-sandbox/file?path=CLAUDE.md" % me["id"], A)[1]["text"]
        for phrase in ("TEAM SANDBOX", "not the user's personal computer", "not the owner's", "only inside this repository",
                       "Never try to escape", "refuse and say why", "monitoring notice"):
            self.assertIn(phrase, rules)
        readme = self.api("GET", "/api/sandboxes/%s/my-sandbox/file?path=README.md" % me["id"], A)[1]["text"]
        self.assertIn("guardrail files are guidance, not the protection", readme)
        self.assertIn("Not your computer, not the owner's computer", readme)
        print("\n  default sandbox = exactly the %d starter files; guardrails + README wording present" % len(files))

    def test_11_new_sandbox_and_names(self):
        s, j = self.api("POST", "/api/sandboxes", A, {"name": "Weather Bot"})
        self.assertEqual((s, j["sandbox"]["name"]), (200, "weather-bot"))
        s, j = self.api("POST", "/api/sandboxes", A, {"name": "weather-bot"})
        self.assertEqual(s, 400)
        s, j = self.api("POST", "/api/sandboxes", A, {"name": "../../etc"})
        self.assertEqual((s, j.get("sandbox", {}).get("name")), (200, "etc"), "path tricks are flattened into a plain name")

    def test_12_member_isolation_api(self):
        a, b = self.me(A)["id"], self.me(B)["id"]
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, A)[0], 200)
        for path in ("/api/sandboxes/%s/my-sandbox" % a, "/api/sandboxes/%s/my-sandbox/file?path=README.md" % a):
            s, j = self.api("GET", path, B)
            self.assertEqual(s, 404, "B can not see A's sandbox: %s -> %s" % (path, s))
        s, _ = self.api("POST", "/api/sandboxes/%s/my-sandbox/test" % a, B)
        self.assertEqual(s, 404)
        s, j = self.api("POST", "/api/builds", B, {"sandbox": "my-sandbox", "task": "x", "model": "builder-bot", "owner": a})
        self.assertNotEqual(j.get("build", {}).get("owner"), a, "a build always runs in the caller's OWN sandbox")
        if s == 200:
            self.wait_build(B, j["build"]["id"])
        names = [x["owner"] for x in self.api("GET", "/api/sandboxes?all=1", B)[1]["sandboxes"]]
        self.assertEqual(set(names), {b}, "?all=1 is ignored for members")
        s, j = self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, ADMIN)
        self.assertEqual(s, 200, "admins see everyone's sandboxes")
        self.assertIn(a, [x["owner"] for x in self.api("GET", "/api/sandboxes?all=1", ADMIN)[1]["sandboxes"]])
        s, j = self.api("GET", "/api/admin/members", A)
        self.assertEqual(s, 403)
        s, j = self.api("GET", "/api/tripwire", A)
        self.assertEqual(s, 403)
        print("\n  isolation (API): B gets 404 on A's sandbox/file/tests; admin sees all; member gets 403 on admin endpoints")

    def test_13_csrf_header_required(self):
        s, j = self.api("POST", "/api/sandboxes", A, {"name": "csrf-test"}, csrf=False)
        self.assertEqual(s, 403)
        self.assertIn("X-Teambox", j["error"])

    # ============================================================ 3. AI builds, checkpoints, tripwire, promote
    def test_20_ai_build_checkpoints_gateway_account(self):
        a = self.me(A)["id"]
        n0 = len(self.gw.calls)
        bid = self.start_build(A, "NORMAL: add an add() function with a test")
        b = self.wait_build(A, bid)
        self.assertEqual(b["status"], "waiting for review", b)
        self.assertEqual(b["verdict"], "tests pass", b.get("tests_text"))
        self.assertEqual(len(b["checkpoints"]), 2, "one checkpoint per accepted write")
        self.assertEqual({f["path"] for f in b["files"]}, {"add.py", "test_add.py"})
        self.assertIn("+def add(a, b):", b["diff"])
        chat_calls = [c for c in self.gw.calls[n0:] if c["path"] == "/v1/chat/completions"]
        self.assertTrue(chat_calls and all(c["account"] == A for c in chat_calls), "every model call carried X-Team-Account = the member")
        self.assertTrue(all(m["role"] in ("system", "user", "assistant") for c in chat_calls for m in c["body"]["messages"]),
                        "only roles the real gateway accepts")
        sb = self.app.sb.path(a, "my-sandbox")
        refs = subprocess.run(["git", "-C", sb, "for-each-ref", "--format=%(refname)", "refs/teambox/"], capture_output=True, text=True).stdout
        self.assertIn("refs/teambox/builds/" + bid, refs)
        self.assertIn("refs/teambox/cp/%s/1" % bid, refs)
        self.assertEqual(subprocess.run(["git", "-C", sb, "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(), "main",
                         "the sandbox is back on main after the build")
        type(self).normal_build = bid
        print("\n  AI build %s: 2 checkpoints, tests pass in the jail, %d model calls all as %s" % (bid, len(chat_calls), A))

    def test_21_one_build_per_member(self):
        self.gw.slow_s = 1.5
        bid = self.start_build(B, "SLOW NORMAL build")
        s, j = self.api("POST", "/api/builds", B, {"sandbox": "my-sandbox", "task": "second", "model": "builder-bot"})
        self.assertEqual(s, 409, j)
        self.assertIn("one at a time", j["error"])
        self.wait_build(B, bid, 120)
        self.gw.slow_s = 0
        print("\n  one build per member: second start refused: %s" % j["error"])

    def test_22_tripwire_never_blocks_run(self):
        bid = self.start_build(A, "TRIPWIRE: write a test")
        b = self.wait_build(A, bid)
        blocked = [t for t in b["transcript"] if t.get("kind") == "tool" and "BLOCKED by the safety check" in (t.get("result") or "")]
        self.assertTrue(blocked, "the tripwire fired")
        msg = blocked[0]["result"]
        self.assertIn("test_trip.py line 7 contains 'nsenter'", msg)
        self.assertIn("tries to switch namespaces", msg)
        self.assertIn("do NOT wait", msg)
        self.assertEqual(b["status"], "waiting for review", "the run continued and finished")
        self.assertEqual(b["verdict"], "tests pass")
        s, t = self.api("GET", "/api/tripwire", ADMIN)
        pend = [p for p in t["pending"] if p["build"] == bid]
        self.assertTrue(pend)
        self.assertEqual(self.api("POST", "/api/tripwire/%s/letpass" % pend[0]["sha"], A)[0], 403, "members can not let it pass")
        s, j = self.api("POST", "/api/tripwire/%s/letpass" % pend[0]["sha"], ADMIN)
        self.assertEqual(s, 200)
        print("\n  tripwire message: %s" % msg[:200])

    def test_23_builder_path_guard(self):
        bid = self.start_build(A, "GUARD probes")
        b = self.wait_build(A, bid)
        res = [t["result"] for t in b["transcript"] if t.get("kind") == "tool"]
        self.assertTrue(res[0].startswith("REFUSED") and "relative to the sandbox" in res[0], res[0])
        self.assertIn("guardrails", res[1])
        self.assertIn(".git folder is off limits", res[2])

    def test_24_promote_admin_only(self):
        if not hasattr(type(self), "normal_build"):
            self.test_20_ai_build_checkpoints_gateway_account()
        bid = type(self).normal_build
        s, b = self.api("GET", "/api/builds/" + bid, A)
        head = b["head"]
        self.assertFalse(b["can_promote"])
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, A, {"confirm": True, "head": head})
        self.assertEqual(s, 403, "the owner (a member) can not promote")
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, B, {"confirm": True, "head": head})
        self.assertIn(s, (403, 404), "another member can not promote")
        s, j = self.api("POST", "/api/builds/%s/reject" % bid, B)
        self.assertEqual(s, 404, "another member can not even see (or reject) it")
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, ADMIN, {"head": head})
        self.assertEqual(s, 400, "admin must confirm")
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, ADMIN, {"confirm": True, "head": "0" * 40})
        self.assertEqual(s, 409, "admin must confirm the exact reviewed head")
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, ADMIN, {"confirm": True, "head": head})
        self.assertEqual(s, 200, j)
        self.assertEqual(j["build"]["status"], "promoted")
        tm = self.app.sb.team_main
        self.assertTrue(os.path.exists(os.path.join(tm, "add.py")), "the build is merged into data/team-main")
        log = subprocess.run(["git", "-C", tm, "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
        self.assertIn("Promote %s" % bid, log)
        print("\n  promote: member 403, other member 403/404, admin w/o confirm 400, wrong head 409, admin OK -> team-main: %s" % log.strip())

    def test_25_member_rejects_own_only(self):
        bid = self.start_build(B, "NORMAL again")
        self.wait_build(B, bid)
        s, j = self.api("POST", "/api/builds/%s/reject" % bid, A)
        self.assertEqual(s, 404)
        s, j = self.api("POST", "/api/builds/%s/reject" % bid, B)
        self.assertEqual((s, j["build"]["status"]), (200, "rejected"))
        s, j = self.api("POST", "/api/builds/%s/promote" % bid, ADMIN, {"confirm": True, "head": j["build"]["head"]})
        self.assertEqual(s, 409, "a rejected build can not be promoted")

    # ============================================================ 4. the jail
    def jail_run(self, code, src=None):
        d = src or tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(d, "probe.py"), "w") as fh:
            fh.write(code)
        return jail.run(self.cfg["jail"], d, ["python3", "probe.py"], os.path.join(self.tmp, "jt"))

    def test_30_jail_blocks_network(self):
        r = self.jail_run("import socket\ntry:\n    socket.create_connection(('1.1.1.1', 80), timeout=3)\n    print('NET-OPEN')\nexcept OSError as e:\n    print('NET-BLOCKED', e)\n"
                          "try:\n    socket.create_connection(('127.0.0.1', %d), timeout=3)\n    print('HOST-LOOPBACK-OPEN')\nexcept OSError as e:\n    print('HOST-LOOPBACK-BLOCKED', e)\n" % self.port)
        self.assertIn("NET-BLOCKED", r["output"])
        self.assertIn("HOST-LOOPBACK-BLOCKED", r["output"], "the jail can not reach the team box app on the host's loopback either")
        print("\n  jail network: %s" % r["output"].strip().replace("\n", " | "))

    def test_31_jail_filesystem(self):
        a = self.me(A)["id"]
        other = os.path.join(self.app.sb.path(a, "my-sandbox"), "README.md")
        r = self.jail_run("import os\nprint('ROOT', sorted(os.listdir('/')))\nprint('PASSWD', open('/etc/passwd').read().count(chr(10)))\n"
                          "for p in [%r, %r, '/home', '/root', '/var', '/opt', '/mnt']:\n    print('SEE', p, os.path.exists(p))\n"
                          "try:\n    open('/usr/bin/evil', 'w')\n    print('USR-WRITABLE')\nexcept OSError as e:\n    print('USR-READONLY', e.errno)\n"
                          "open('/work/ok.txt', 'w').write('x')\nprint('WORK-WRITABLE')\nprint('UID', os.getuid())\n" % (other, self.tmp))
        out = r["output"]
        self.assertIn("SEE %s False" % other, out, "another member's sandbox is not visible")
        self.assertIn("SEE %s False" % self.tmp, out, "the box's data folder is not visible")
        for p in ("/home", "/root", "/var", "/opt", "/mnt"):
            self.assertIn("SEE %s False" % p, out)
        self.assertIn("USR-READONLY", out)
        self.assertIn("WORK-WRITABLE", out)
        self.assertIn("PASSWD 3", out, "only the jail's tiny fake /etc/passwd")
        print("\n  jail filesystem: %s" % " | ".join(ln for ln in out.splitlines() if ln.startswith(("ROOT", "USR", "UID"))))

    def test_32_jail_memory_and_time(self):
        r = self.jail_run("try:\n    b = bytearray(2 * 1024 ** 3)\n    print('ALLOC-OK')\nexcept MemoryError:\n    print('MEMORY-CAPPED')\n")
        self.assertIn("MEMORY-CAPPED", r["output"])
        j2 = dict(self.cfg["jail"], timeout_s=3)
        d = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(d, "s.py"), "w") as fh:
            fh.write("import time\ntime.sleep(60)\n")
        t0 = time.time()
        r = jail.run(j2, d, ["python3", "s.py"], os.path.join(self.tmp, "jt"))
        self.assertTrue(r["timed_out"] and time.time() - t0 < 15, r)
        print("\n  jail caps: 2 GB alloc -> MemoryError; sleep 60 -> killed after %.1f s" % (time.time() - t0))

    def test_33_jail_refuses_without_bwrap(self):
        with self.assertRaises(jail.JailError):
            jail.run(dict(self.cfg["jail"], bwrap="/nonexistent/bwrap"), self.tmp, ["true"], os.path.join(self.tmp, "jt"))

    # ============================================================ 5. git: bring your own AI tool
    def test_40_git_push_with_token(self):
        a, b = self.me(A)["id"], self.me(B)["id"]
        self.api("POST", "/api/sandboxes", A, {"name": "git-demo"})
        _tid, tok = self.token(A, "laptop")
        d = tempfile.mkdtemp(dir=self.tmp)
        r = self.git("clone", self.url(a, "git-demo"), os.path.join(d, "noauth"))
        self.assertNotEqual(r.returncode, 0, "clone WITHOUT a token fails")
        r = self.git("clone", self.url(a, "git-demo", "tbx_wrongtoken"), os.path.join(d, "wrong"))
        self.assertNotEqual(r.returncode, 0, "clone with a wrong token fails")
        w = os.path.join(d, "a")
        r = self.git("clone", self.url(a, "git-demo", tok), w)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(w, "CLAUDE.md")))
        with open(os.path.join(w, "mul.py"), "w") as fh:
            fh.write("def mul(a, b):\n    return a * b\n")
        with open(os.path.join(w, "test_mul.py"), "w") as fh:
            fh.write("import unittest\nfrom mul import mul\n\n\nclass T(unittest.TestCase):\n    def test(self):\n        self.assertEqual(mul(3, 4), 12)\n")
        self.git("add", "-A", cwd=w)
        self.git("commit", "-qm", "add mul", cwd=w)
        r = self.git("push", "origin", "main", cwd=w)
        self.assertEqual(r.returncode, 0, r.stderr)
        sb = self.app.sb.path(a, "git-demo")
        self.assertTrue(os.path.exists(os.path.join(sb, "mul.py")), "the push updated the sandbox's working tree")
        pushes = [x for x in self.api("GET", "/api/builds", A)[1]["builds"] if x["kind"] == "push" and x["sandbox"] == "git-demo"]
        self.assertTrue(pushes)
        pb = self.wait_build(A, pushes[0]["id"])
        self.assertEqual((pb["status"], pb["verdict"]), ("waiting for review", "tests pass"), pb.get("tests_text"))
        self.assertIn("test (test_mul.T", pb["tests_text"])
        # B: own token can not reach A's sandbox, read or write
        _t, btok = self.token(B, "b")
        r = self.git("clone", self.url(a, "git-demo", btok), os.path.join(d, "b"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("403", r.stderr)
        r = self.git("push", self.url(a, "git-demo", btok), "main", cwd=w)
        self.assertNotEqual(r.returncode, 0, "B can not push to A's sandbox")
        # a hidden ref (what the admin reviews) can not be overwritten by a push
        r = self.git("push", self.url(a, "git-demo", tok), "HEAD:refs/teambox/builds/" + pushes[0]["id"], cwd=w)
        self.assertNotEqual(r.returncode, 0, "refs/teambox/* is not pushable")
        # admin token: may clone (admins see all), may not push
        _t, atok = self.token(ADMIN, "admin")
        r = self.git("clone", self.url(a, "git-demo", atok), os.path.join(d, "adm"))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.git("push", self.url(a, "git-demo", atok), "main", cwd=os.path.join(d, "adm"))
        self.assertNotEqual(r.returncode, 0)
        type(self).push_build = pushes[0]["id"]
        print("\n  git: no token/wrong token refused; token clone+push OK; push tests in jail: %s; B 403; hidden ref push refused; admin read-only"
              % pb["verdict"])

    def test_41_revoked_token_fails(self):
        a = self.me(A)["id"]
        tid, tok = self.token(A, "temp")
        d = tempfile.mkdtemp(dir=self.tmp)
        self.assertEqual(self.git("clone", self.url(a, "my-sandbox", tok), os.path.join(d, "1")).returncode, 0)
        toks = self.api("GET", "/api/tokens", A)[1]["tokens"]
        self.assertTrue(all("hash" not in t and "token" not in t for t in toks), "token list never shows the token or its hash")
        raw = open(os.path.join(self.cfg["data_dir"], "members", a, "tokens.json")).read()
        self.assertNotIn(tok, raw, "the token is stored hashed only")
        self.assertEqual(self.api("POST", "/api/tokens/%s/revoke" % tid, A)[0], 200)
        r = self.git("clone", self.url(a, "my-sandbox", tok), os.path.join(d, "2"))
        self.assertNotEqual(r.returncode, 0, "a revoked token stops working")

    def test_42_push_escape_test_results(self):
        a = self.me(A)["id"]
        self.api("POST", "/api/sandboxes", A, {"name": "escape-demo"})
        _t, tok = self.token(A, "esc")
        w = os.path.join(tempfile.mkdtemp(dir=self.tmp), "w")
        self.assertEqual(self.git("clone", self.url(a, "escape-demo", tok), w).returncode, 0)
        with open(os.path.join(w, "test_escape.py"), "w") as fh:
            fh.write("import os, socket, unittest\n\n\nclass E(unittest.TestCase):\n    def test_net(self):\n"
                     "        socket.create_connection(('example.com', 443), timeout=3)\n    def test_peek(self):\n"
                     "        print('PEEK', os.path.exists(%r))\n" % self.cfg["data_dir"])
        self.git("add", "-A", cwd=w)
        self.git("commit", "-qm", "try to escape", cwd=w)
        self.assertEqual(self.git("push", "origin", "main", cwd=w).returncode, 0)
        pid = [x for x in self.api("GET", "/api/builds", A)[1]["builds"] if x["sandbox"] == "escape-demo"][0]["id"]
        pb = self.wait_build(A, pid)
        self.assertEqual(pb["verdict"], "tests FAIL")
        self.assertRegex(pb["tests_text"], r"(gaierror|Temporary failure|unreachable|Name or service)")
        self.assertIn("PEEK False", pb["tests_text"])
        print("\n  pushed escape test -> %s; network error shown; data dir invisible" % pb["verdict"])

    # ============================================================ 6. models, versions, chat
    def test_50_versions_keep_original(self):
        s, v = self.api("GET", "/api/versions?model=chat-llama", A)
        self.assertEqual(v["versions"][0]["id"], "original")
        self.assertEqual(v["versions"][0]["name"], "Original")
        s, j = self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Original", "settings": {"temperature": 0.1}})
        self.assertEqual(s, 400, "a version can not take the name Original")
        s, j = self.api("POST", "/api/versions/delete", A, {"model": "chat-llama", "id": "original"})
        self.assertEqual(s, 400, "Original can not be deleted")
        s, j = self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Pirate", "settings": {"system_prompt": "Talk like a pirate.", "temperature": "0.2", "max_tokens": 50, "ctx": 2048}})
        self.assertEqual(s, 200, j)
        vid = j["version"]["id"]
        s, v = self.api("GET", "/api/versions?model=chat-llama", A)
        self.assertEqual([x["name"] for x in v["versions"]], ["Original", "Pirate"])
        self.assertEqual(self.api("GET", "/api/versions?model=chat-llama", B)[1]["versions"][1:], [], "versions are per member")
        s, c = self.api("POST", "/api/chats", A, {"model": "chat-llama", "version": vid})
        n0 = len(self.gw.calls)
        s, r = self.api("POST", "/api/chats/%s/send" % c["chat"]["id"], A, {"text": "hello"})
        self.assertEqual(s, 200, r)
        self.assertTrue(r.get("answer"), r)
        call = [x for x in self.gw.calls[n0:] if x["path"] == "/v1/chat/completions"][0]
        self.assertEqual(call["body"]["messages"][0], {"role": "system", "content": "Talk like a pirate."})
        self.assertEqual((call["body"]["temperature"], call["body"]["max_tokens"], call["account"]), (0.2, 50, A))
        s, c2 = self.api("POST", "/api/chats", A, {"model": "chat-llama", "version": "original"})
        self.api("POST", "/api/chats/%s/send" % c2["chat"]["id"], A, {"text": "hi"})
        call = [x for x in self.gw.calls if x["path"] == "/v1/chat/completions"][-1]
        self.assertNotIn("temperature", call["body"])
        self.assertEqual(call["body"]["messages"][0]["role"], "user", "Original = no added system prompt, model defaults")
        self.assertEqual(self.api("GET", "/api/chats/" + c["chat"]["id"], B)[0], 404, "B can not read A's chat")
        self.assertIn(c["chat"]["id"], [x["id"] for x in self.api("GET", "/api/chats", A)[1]["chats"]], "chat saved under the profile")
        print("\n  versions: Original always first, undeletable, name reserved; 'Pirate' applied (system prompt, temp 0.2, 50 tokens); answer: %r" % r["answer"][:40])

    def test_52_deleted_version_chat_falls_back_to_original(self):
        """Button check 2026-10-21: a chat tied to a deleted settings version could never send again (every Send = 400)."""
        s, j = self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Shortlived", "settings": {"system_prompt": "Be brief.", "temperature": 0.3}})
        self.assertEqual(s, 200, j)
        vid = j["version"]["id"]
        s, c = self.api("POST", "/api/chats", A, {"model": "chat-llama", "version": vid})
        cid = c["chat"]["id"]
        s, g = self.api("GET", "/api/chats/" + cid, A)
        self.assertEqual((s, g["version_name"], g["version_missing"]), (200, "Shortlived", False), g)
        s, d = self.api("POST", "/api/versions/delete", A, {"model": "chat-llama", "id": vid})
        self.assertEqual((s, d.get("ok")), (200, True), d)
        s, g = self.api("GET", "/api/chats/" + cid, A)
        self.assertEqual(s, 200, g)
        self.assertTrue(g["version_missing"], "the page is told the version is gone (it shows the note)")
        self.assertEqual(g["versions"][0], {"id": "original", "name": "Original"}, "the picker always offers Original")
        n0 = len(self.gw.calls)
        s, r = self.api("POST", "/api/chats/%s/send" % cid, A, {"text": "still there?"})
        self.assertEqual(s, 200, "the chat can send again (was 400 'no such settings version')")
        self.assertTrue(r.get("answer"), r)
        self.assertTrue(r["version_fallback"])
        self.assertEqual((r["chat"]["version"], r["version_name"], r["version_missing"]), ("original", "Original", False))
        notes = [m["content"] for m in r["chat"]["messages"] if m["role"] == "notice"]
        self.assertTrue(any("was deleted" in n and "Original" in n for n in notes), notes)
        call = [x for x in self.gw.calls[n0:] if x["path"] == "/v1/chat/completions"][0]
        self.assertEqual(call["body"]["messages"][0]["role"], "user", "Original = no system prompt from the deleted version")
        self.assertNotIn("temperature", call["body"])
        self.assertNotIn("notice", [m["role"] for m in call["body"]["messages"]], "the note is never sent to the model")
        s, r2 = self.api("POST", "/api/chats/%s/send" % cid, A, {"text": "again"})
        self.assertEqual((s, r2["version_fallback"]), (200, False), "the note is written once")
        # the member can pick a version for the chat
        s, j = self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Formal", "settings": {"system_prompt": "Be formal."}})
        v2 = j["version"]["id"]
        s, p = self.api("POST", "/api/chats/%s/version" % cid, A, {"version": "nope"})
        self.assertEqual(s, 400, "an unknown version is refused")
        s, p = self.api("POST", "/api/chats/%s/version" % cid, A, {"version": v2})
        self.assertEqual((s, p["chat"]["version"], p["version_name"]), (200, v2, "Formal"), p)
        self.assertEqual(self.api("POST", "/api/chats/%s/version" % cid, B, {"version": "original"})[0], 404, "B can not change A's chat")
        n1 = len(self.gw.calls)
        self.api("POST", "/api/chats/%s/send" % cid, A, {"text": "and now?"})
        call = [x for x in self.gw.calls[n1:] if x["path"] == "/v1/chat/completions"][0]
        self.assertEqual(call["body"]["messages"][0], {"role": "system", "content": "Be formal."})
        print("\n  deleted settings version: chat falls back to Original with a note, sends again; member picked 'Formal' for it")

    def test_51_gateway_refusal_shown_plainly(self):
        s, j = self.api("POST", "/api/models/start", A, {"model": "big-model"})
        self.assertEqual(s, 200)
        self.assertFalse(j["ok"])
        self.assertEqual(j["refused"], "'big-model' needs ~17.1 GB RAM; your account uses 0.0 of its 1.0 GB cap")
        s, j = self.api("POST", "/api/models/stop", A, {"model": "chat-llama"})
        self.assertIn("only Marjus can stop it", j["refused"])
        s, j = self.api("GET", "/api/models", "zed@test.example")
        self.assertFalse(j["ok"])
        self.assertIn("is not in the team policy", j["refused"])
        print("\n  gateway refusals passed through word for word: %s" % j["refused"])

    # ============================================================ 7. tour + public pages + notice
    def test_60_tour(self):
        s, me = self.api("GET", "/api/me", "newbie@test.example")
        self.assertTrue(me["first_visit"], "a new member is sent to the tour")
        s, t = self.api("GET", "/api/tour", "newbie@test.example")
        self.assertEqual(t["bottom_text"], EXACT_BOTTOM)
        self.assertEqual([c["id"] for c in t["checkpoints"]], ["home", "projects", "builds", "models", "byo", "vault", "messages", "caps", "people", "promote"])
        self.assertTrue(all(c["link"].startswith("#/") for c in t["checkpoints"]), "every checkpoint is clickable")
        for claim in ("Project isolation", "The jail", "Git tokens", "Caps", "Who can promote", "encrypted at rest", "Not my computer", "can NOT", "PARTLY"):
            self.assertIn(claim, t["prompt"])
        self.assertIn("/how-it-works", t["prompt"])
        # Director merge 2026-10-21: feeds + messages are all live now, so the prompt must say LIVE for each and nothing is "not live yet"
        for claim in ("9. Share to feed: LIVE", "10. Messages, calls and video calls with a must-accept monitoring notice: LIVE", "12. Home feed uploads: LIVE"):
            self.assertIn(claim, t["prompt"], "the prompt states what is live")
        self.assertNotIn("NOT LIVE YET", t["prompt"])
        self.api("POST", "/api/tour", "newbie@test.example", {"done": "projects"})
        self.api("POST", "/api/tour", "newbie@test.example", {"done": "nonsense"})
        s, me = self.api("GET", "/api/me", "newbie@test.example")
        self.assertEqual(me["tour"]["done"], ["projects"], "progress remembered (unknown ids ignored)")
        self.api("POST", "/api/tour", "newbie@test.example", {"seen": True})
        self.assertFalse(self.api("GET", "/api/me", "newbie@test.example")[1]["first_visit"])
        js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf8").read()
        self.assertIn("t.bottom_text", js)
        self.assertIn("Copy text", js)
        self.assertEqual(tour.BOTTOM_TEXT, EXACT_BOTTOM)
        print("\n  tour: first visit flagged, 9 clickable checkpoints, progress saved, exact bottom text, honest prompt (%d chars)" % len(t["prompt"]))

    def test_61_public_pages(self):
        s, page = self.api("GET", "/how-it-works")
        self.assertEqual(s, 200)
        self.assertIn("How the team box works", page)
        s, t = self.api("GET", "/api/public/tour")
        self.assertEqual((s, t["bottom_text"]), (200, EXACT_BOTTOM))
        s, n = self.api("GET", "/api/public/notice")
        self.assertEqual(n["notice"], messages.NOTICE)
        s, j = self.api("GET", "/api/sandboxes")
        self.assertEqual(s, 401, "everything else needs a signed-in identity")

    def test_62_notice_text_exact_fixes(self):
        n = messages.NOTICE
        for fixed in ("strictly prohibited", "supersede one's interests"):
            self.assertIn(fixed, n)
        for typo in ("sctrictly", "intersts", "supercede"):
            self.assertNotIn(typo, n)
        src = open(r"/mnt/c/Users/gjoka/xOS-prep/STATE/DIRECTIVES/2026-10-21-team-calls-messages-monitoring-notice.txt", encoding="utf8").read() \
            if os.path.exists("/mnt/c/Users/gjoka/xOS-prep/STATE/DIRECTIVES") else None
        if src:
            start = src.index('"chatting') + 1
            orig = src[start:src.index('promote it as one winning means the team is winning.') + len('promote it as one winning means the team is winning.')]
            fixed = orig.replace("sctrictly", "strictly").replace("intersts", "interests").replace("supercede", "supersede")
            self.assertEqual(n, fixed, "the notice is Marjus's text with exactly the three approved fixes")
            print("\n  notice == directive text with exactly the 3 approved typo fixes")

    def test_63_modules_coming_soon(self):
        s, j = self.api("GET", "/api/modules", A)
        st = {m["id"]: m["status"] for m in j["modules"]}
        self.assertEqual(st, {"feed": "live", "messages": "live"}, "home feed + messages are live; the weekly team feed is gone (2026-10-21)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
