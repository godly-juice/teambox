"""Sign-in (invite code -> PIN), sessions, the admin invite API, the vault, Lumi's welcome. Run on Linux:
    python3 -m unittest -v tests.test_auth
The REAL team box runs in-process with dev_mode OFF (real sessions, no Cloudflare Access header anywhere), a TEST Lumi key made here,
the fake team gateway + BESNIK's fake llama for the welcome chat. The browser's vault scheme (static/vault.js) is re-done in Python
with the `cryptography` package (test-only). Never calls a real model, never sends anything anywhere."""
import base64
import hashlib
import json
import os
import re
import shutil
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

import comms_crypto as K  # noqa: E402
from cryptography.exceptions import InvalidTag  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from fake_gateway import FakeGateway  # noqa: E402
from test_teambox import EXACT_BOTTOM, free_port  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox import tour, welcome  # noqa: E402
from teambox.server import make_server  # noqa: E402

VALG = "AES-256-GCM(content) + AES-256-GCM(vault key) + RSA-OAEP-3072-SHA256(Lumi)"
ANA, MARJUS = "ana-kola@team.xseed.ing", "marjus@team.xseed.ing"
PURPOSE = "build the sales dashboard for our shop"


def b64(b):
    return base64.b64encode(b).decode()


class Browser:
    """A tiny cookie-keeping client (one per person/device). ip -> CF-Connecting-IP (the peer is the local 'cloudflared')."""
    def __init__(self, base, ip="203.0.113.10"):
        self.base, self.ip, self.cookies, self.raw_cookies = base, ip, {}, []

    def req(self, method, path, body=None, headers=None):
        h = {"CF-Connecting-IP": self.ip}
        h.update(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        data = None
        if method == "POST":
            h.setdefault("X-Teambox", "1")
            h["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        r = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            resp = urllib.request.urlopen(r, timeout=60)
            code, hdrs, raw = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            code, hdrs, raw = e.code, e.headers, e.read()
        for sc in hdrs.get_all("Set-Cookie") or []:
            self.raw_cookies.append(sc)
            k, _, v = sc.split(";")[0].partition("=")
            if "Max-Age=0" in sc or not v:
                self.cookies.pop(k, None)
            else:
                self.cookies[k] = v
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw.decode("utf8", "replace")


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="teambox-auth-")
        cls.lumi = K.Key()
        cls.export_token = "lce_test_" + os.urandom(16).hex()
        cls.secret = "test-secret-" + os.urandom(8).hex()
        sf = os.path.join(cls.tmp, "gateway.secret")
        with open(sf, "w") as fh:
            fh.write(cls.secret)
        cls.llama_port = free_port()
        cls.llama = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_llama.py"), "--port", str(cls.llama_port), "--tps", "200"])
        cls.gw = FakeGateway(cls.secret, [ANA, MARJUS, "bo@team.xseed.ing"], cls.llama_port)
        cls.port = free_port()
        cls.cfgp = os.path.join(cls.tmp, "config.json")
        with open(cls.cfgp, "w") as fh:
            json.dump({"port": cls.port, "data_dir": os.path.join(cls.tmp, "data"), "public_url": "https://team.xseed.ing", "dev_mode": False,
                       "gateway": {"url": "http://127.0.0.1:%d" % cls.gw.port, "secret_file": sf, "timeout_s": 30},
                       "auth": {"scrypt_n": 4096, "ip_max_fails": 8, "account_max_fails": 5, "lock_minutes": 15},
                       "welcome": {"model": "chat-llama"},
                       "comms": {"lumi_public_key": cls.lumi.spki, "lumi_kid": cls.lumi.kid,
                                 "export_token_sha256": hashlib.sha256(cls.export_token.encode()).hexdigest()}}, fh)
        os.environ.pop("TEAMBOX_ALLOW_DEV", None)
        cls.cfg = C.load(cls.cfgp)
        cls.app, cls.srv = make_server(cls.cfg)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/healthz", timeout=1)
                urllib.request.urlopen("http://127.0.0.1:%d/health" % cls.llama_port, timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        # the install step: Marjus's admin invite (CLI) + Lumi's API token file (CLI), exactly as INSTALL-XOS.md 3b says
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "teambox.auth", "--config", cls.cfgp, "admin-invite", "--name", "Marjus"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 0, r.stderr
        cls.admin_cli_out = r.stdout
        cls.admin_code = re.search(r"code:\s+(\d{4} \d{4} \d{4})", r.stdout).group(1)
        cls.tokfile = os.path.join(cls.tmp, "invite-api.token")
        r = subprocess.run([sys.executable, "-m", "teambox.auth", "--config", cls.cfgp, "new-api-token", "--out", cls.tokfile], cwd=ROOT,
                           capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 0, r.stderr
        cls.token_cli_out = r.stdout
        with open(cls.tokfile) as fh:
            cls.api_token = fh.read().strip()
        cls.app.auth.__init__(cls.cfg, cls.app.store, cls.app.log)       # re-read files written by the CLI processes
        cls.marjus = cls.setup_person(cls.admin_code, "marjus", "40817263", ip="198.51.100.1")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.gw.stop()
        cls.llama.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    @classmethod
    def setup_person(cls, code, ident, pin, ip="203.0.113.10"):
        b = Browser(cls.base, ip)
        s, j = b.req("POST", "/api/auth/join", {"code": code})
        assert s == 200, j
        s, j = b.req("POST", "/api/auth/setup", {"identifier": ident, "pin": pin, "pin2": pin})
        assert s == 200, j
        return b

    def bearer(self, method, path, body=None, token=None, ip="192.0.2.50"):
        return Browser(self.base, ip).req(method, path, body, {"Authorization": "Bearer " + (token or self.api_token)})

    def invite(self, name, purpose=PURPOSE, **kw):
        s, j = self.bearer("POST", "/api/admin/invites", dict(name=name, purpose=purpose, **kw))
        self.assertEqual(s, 201, j)
        return j

    def vault_init(self, b, password):
        vk = os.urandom(32)
        salt, iv = os.urandom(16), os.urandom(12)
        kek = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000, 32)
        s, j = b.req("POST", "/api/vault/init", {"kdf": {"name": "PBKDF2-SHA256", "iter": 600000, "salt": b64(salt)},
                                                 "pw": {"iv": b64(iv), "wk": b64(AESGCM(kek).encrypt(iv, vk, None))}})
        self.assertEqual(s, 200, j)
        return vk

    @staticmethod
    def open_vk(rec, password):
        kek = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(rec["kdf"]["salt"]), rec["kdf"]["iter"], 32)
        return AESGCM(kek).decrypt(base64.b64decode(rec["pw"]["iv"]), base64.b64decode(rec["pw"]["wk"]), None)

    def seal(self, vk, obj, lumi=True, extra=None, ck=None):
        ck = ck or AESGCM.generate_key(256)
        iv, kiv = os.urandom(12), os.urandom(12)
        keys = [{"to": "vault", "iv": b64(kiv), "wk": b64(AESGCM(vk).encrypt(kiv, ck, None))}]
        if lumi:
            keys.append({"to": "lumi", "kid": self.lumi.kid, "wk": b64(K.wrap(ck, self.lumi.spki))})
        keys += extra or []
        return {"v": 1, "alg": VALG, "iv": b64(iv), "ct": b64(AESGCM(ck).encrypt(iv, json.dumps(obj).encode(), None)), "keys": keys}, ck

    # ============================================================ install: admin account + API token
    def test_00_install_cli(self):
        self.assertIn("/join", self.admin_cli_out)
        self.assertNotIn(self.api_token, self.token_cli_out, "the API token is never printed")
        self.assertEqual(oct(os.stat(self.tokfile).st_mode & 0o777), "0o600")
        raw = open(os.path.join(self.cfg["data_dir"], "auth", "api-tokens.json")).read()
        self.assertNotIn(self.api_token, raw, "stored as sha256 only")
        self.assertIn(hashlib.sha256(self.api_token.encode()).hexdigest(), raw)
        s, me = self.marjus.req("GET", "/api/me")
        self.assertEqual((s, me["email"], me["admin"], me["signed_in_with"]), (200, MARJUS, True, "session"))
        print("\n  install: admin-invite CLI printed a one-time code -> Marjus joined as admin (%s); API token file 0600, never printed, "
              "stored as sha256" % MARJUS)

    # ============================================================ F. the admin-only invite API
    def test_01_invite_api_admin_only(self):
        s, j = self.bearer("GET", "/api/admin/invites", token="tbxadm_wrong")
        self.assertEqual(s, 401, j)
        s, j = self.bearer("GET", "/api/admin/invites", token="not-even-a-token")
        self.assertEqual(s, 401)
        s, j = self.bearer("GET", "/api/admin/accounts")
        self.assertEqual(s, 401, "the API token reaches ONLY the invite endpoints")
        s, j = Browser(self.base).req("POST", "/api/admin/invites", {"name": "x", "purpose": "y"})
        self.assertEqual(s, 401, "no session, no token")
        j = self.invite("Bo Test", phone="+1 (402) 555-0101", email="bo@example.com", language="sq")
        self.assertRegex(j["code"], r"^\d{4} \d{4} \d{4}$")
        self.assertEqual(j["link"], "https://team.xseed.ing/join")
        self.assertAlmostEqual(j["expires_at"] - time.time(), 48 * 3600, delta=120)
        self.assertEqual(j["account"], "bo-test@team.xseed.ing")
        s, lst = self.bearer("GET", "/api/admin/invites")
        mine = next(i for i in lst["invites"] if i["id"] == j["id"])
        self.assertEqual((mine["status"], mine["language"]), ("pending", "sq"))
        self.assertNotIn(j["code"].replace(" ", ""), json.dumps(lst), "the code is never listed again")
        raw = open(os.path.join(self.cfg["data_dir"], "auth", "invites.json")).read()
        self.assertNotIn(j["code"].replace(" ", ""), raw, "the code is stored hashed")
        # a member can not use the invite endpoints
        bo = self.setup_person(j["code"], "bo-t", "50718293")
        self.assertEqual(bo.req("GET", "/api/admin/invites")[0], 403)
        self.assertEqual(bo.req("POST", "/api/admin/invites", {"name": "x", "purpose": "y"})[0], 403)
        # revoke
        j2 = self.invite("Cy")
        s, r = self.bearer("POST", "/api/admin/invites/%s/revoke" % j2["id"], {})
        self.assertEqual((s, r["status"]), (200, "revoked"))
        self.assertEqual(Browser(self.base, "203.0.113.77").req("POST", "/api/auth/join", {"code": j2["code"]})[0], 400, "revoked = unusable")
        self.assertEqual(self.bearer("POST", "/api/admin/invites/%s/revoke" % j["id"], {})[0], 409, "a used invite can not be revoked")
        self.assertEqual(self.bearer("POST", "/api/admin/invites", {"name": "No Purpose"})[0], 400)
        # Marjus can do the same in the browser (session admin)
        s, j3 = self.marjus.req("POST", "/api/admin/invites", {"name": "Dee", "purpose": "help with design"})
        self.assertEqual(s, 201, j3)
        print("\n  invite API: bad/missing token 401; token only reaches invites; 201 -> 12-digit code once + link + expires in 48 h; "
              "code stored hashed, never listed; member 403; revoke works; used invite 409")

    # ============================================================ A. invite code single-use / expiry / lockout + setup
    def test_02_join_setup_single_use(self):
        j = self.invite("Ana Kola", phone="+14025550123", email="ana@example.com")
        b = Browser(self.base, "203.0.113.20")
        s, r = b.req("POST", "/api/auth/join", {"code": "  " + j["code"].replace(" ", "-") + " "})
        self.assertEqual(s, 200, r)
        self.assertEqual(r["setup"]["purpose"], PURPOSE)
        self.assertIn("14025550123".replace("1", "1"), "".join(r["setup"]["suggestions"]).replace("+", ""))
        setup_cookie = next(c for c in b.raw_cookies if c.startswith("tb_setup="))
        for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
            self.assertIn(flag, setup_cookie)
        self.assertEqual(Browser(self.base, "203.0.113.21").req("POST", "/api/auth/join", {"code": j["code"]})[0], 400, "single use")
        for bad_pin, why in (("123456", "too easy"), ("12345", "too short"), ("abcdefg", "digits"), ("111111", "same"), ("550123", "phone")):
            s, e = b.req("POST", "/api/auth/setup", {"identifier": "ana", "pin": bad_pin})
            self.assertEqual(s, 400, (bad_pin, e))
        self.assertEqual(b.req("POST", "/api/auth/setup", {"identifier": "ana", "pin": "73915864", "pin2": "73915865"})[0], 400)
        self.assertEqual(b.req("POST", "/api/auth/setup", {"identifier": "marjus", "pin": "73915864"})[0], 409, "sign-in name taken")
        s, r = b.req("POST", "/api/auth/setup", {"identifier": "Ana", "pin": "73915864", "pin2": "73915864"})
        self.assertEqual(s, 200, r)
        sess = next(c for c in b.raw_cookies if c.startswith("tb_session="))
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age="):
            self.assertIn(flag, sess)
        self.assertNotIn("tb_setup", b.cookies, "the setup ticket is cleared")
        s, me = b.req("GET", "/api/me")
        self.assertEqual((s, me["email"], me["admin"], me["display_name"], me["setup_next"]), (200, ANA, False, "Ana Kola", "vault"))
        self.assertEqual(me["first_project"]["name"], "sales-dashboard-shop", "the FIRST PROJECT is named from the purpose")
        s, sb = b.req("GET", "/api/sandboxes")
        self.assertEqual([x["name"] for x in sb["sandboxes"]], ["sales-dashboard-shop"], "no extra default sandbox")
        self.assertEqual(b.req("POST", "/api/auth/setup", {"identifier": "ana2", "pin": "73915864"})[0], 401, "setup ticket is single use")
        raw = open(os.path.join(self.cfg["data_dir"], "auth", "accounts.json")).read()
        self.assertNotIn("73915864", raw, "the PIN is stored hashed")
        type(self).ana = b
        print("\n  join: code with spaces/dashes OK, then refused (single use); setup cookie HttpOnly/Secure/Strict; weak/short/non-digit/"
              "repeated/phone PINs refused; session cookie HttpOnly/Secure/SameSite=Lax; first project 'sales-dashboard-shop'")

    def test_03_expiry_and_lockouts(self):
        au = self.app.auth
        # expiry
        j = self.invite("Exp Ired")
        invs = au.invites()
        invs[j["id"]]["expires_at"] = time.time() - 1
        au.save_invites(invs)
        self.assertEqual(Browser(self.base, "203.0.113.30").req("POST", "/api/auth/join", {"code": j["code"]})[0], 400, "expired")
        self.assertEqual(next(i for i in au.list_invites() if i["id"] == j["id"])["status"], "expired")
        # per-invite lockout: wrong codes with this invite's tag
        j = self.invite("Lock Me")
        d = j["code"].replace(" ", "")
        for n in range(5):
            wrong = d[:4] + "%08d" % ((int(d[4:]) + n + 1) % 10 ** 8)
            self.assertEqual(Browser(self.base, "203.0.113.%d" % (40 + n)).req("POST", "/api/auth/join", {"code": wrong})[0], 400)
        self.assertEqual(next(i for i in au.list_invites() if i["id"] == j["id"])["status"], "locked")
        self.assertEqual(Browser(self.base, "203.0.113.49").req("POST", "/api/auth/join", {"code": j["code"]})[0], 400, "locked: right code refused")
        # per-IP block (8 wrong tries from one network in the test config)
        j = self.invite("Ip Test")
        bad = Browser(self.base, "203.0.113.99")
        codes = [bad.req("POST", "/api/auth/join", {"code": "9999 0000 %04d" % n})[0] for n in range(8)]
        self.assertEqual(codes, [400] * 8)
        s, e = bad.req("POST", "/api/auth/join", {"code": j["code"]})
        self.assertEqual(s, 429, "this network is blocked now - even the right code waits: %s" % e)
        self.assertEqual(bad.req("POST", "/api/auth/login", {"identifier": "marjus", "pin": "40817263"})[0], 429, "login blocked from it too")
        self.assertEqual(Browser(self.base, "203.0.113.98").req("POST", "/api/auth/join", {"code": j["code"]})[0], 200, "other networks fine")
        # box-wide pause after many wrong codes
        old = au.join_fails
        au.join_fails = [time.time()] * 500
        try:
            self.assertEqual(Browser(self.base, "203.0.113.97").req("POST", "/api/auth/join", {"code": "1111 2222 3333"})[0], 429)
        finally:
            au.join_fails = old
        print("\n  invite codes: expired 400; 5 wrong tries on one invite -> locked (right code refused); 8 wrong tries from one IP -> 429 "
              "for join + login from that IP only; box-wide pause after many wrong codes -> 429")

    # ============================================================ sign-in / out / timeouts, no Access header
    def test_04_signin_signout_timeouts(self):
        if not hasattr(type(self), "ana"):
            self.test_02_join_setup_single_use()
        au = self.app.auth
        d1, d2 = Browser(self.base, "203.0.113.60"), Browser(self.base, "203.0.113.61")
        self.assertEqual(d1.req("GET", "/api/me")[0], 401)
        for b in (d1, d2):
            s, j = b.req("POST", "/api/auth/login", {"identifier": " ANA ", "pin": "73915864"})
            self.assertEqual(s, 200, j)
        self.assertEqual(d1.req("GET", "/api/me")[1]["email"], ANA, "no Cloudflare Access header anywhere - the app's own sign-in")
        self.assertEqual(Browser(self.base, "203.0.113.62").req("POST", "/api/auth/login", {"identifier": "+1 402 555 0123", "pin": "1"})[0], 401)
        # logout ends only this session
        self.assertEqual(d1.req("POST", "/api/auth/logout", {})[0], 200)
        self.assertEqual(d1.req("GET", "/api/me")[0], 401)
        self.assertEqual(d2.req("GET", "/api/me")[0], 200)
        # sign out everywhere
        d3 = Browser(self.base, "203.0.113.63")
        d3.req("POST", "/api/auth/login", {"identifier": "ana", "pin": "73915864"})
        s, j = d3.req("POST", "/api/auth/logout-all", {})
        self.assertEqual(s, 200)
        self.assertGreaterEqual(j["ended"], 2)
        self.assertEqual((d2.req("GET", "/api/me")[0], d3.req("GET", "/api/me")[0]), (401, 401), "every session ended")
        self.assertEqual(self.ana.req("GET", "/api/me")[0], 401, "including the first browser")
        # idle + absolute timeouts
        for field, back, word in (("last", 241 * 60, "without activity"), ("created", 73 * 3600, "hours")):
            b = Browser(self.base, "203.0.113.64")
            b.req("POST", "/api/auth/login", {"identifier": "ana", "pin": "73915864"})
            h = hashlib.sha256(b.cookies["tb_session"].encode()).hexdigest()
            au.sessions[h][field] -= back
            s, e = b.req("GET", "/api/me")
            self.assertEqual(s, 401)
            self.assertIn(word, e["error"])
        # account lockout after 5 wrong PINs, from 5 different networks
        for n in range(5):
            self.assertEqual(Browser(self.base, "198.51.100.%d" % (10 + n)).req("POST", "/api/auth/login", {"identifier": "ana", "pin": "00000001"})[0], 401)
        s, e = Browser(self.base, "198.51.100.20").req("POST", "/api/auth/login", {"identifier": "ana", "pin": "73915864"})
        self.assertEqual(s, 429, "locked even with the right PIN: %s" % e)
        self.assertEqual(self.marjus.req("POST", "/api/admin/accounts/%s/unlock" % ANA, {})[0], 200)
        type(self).ana = Browser(self.base, "198.51.100.21")
        self.assertEqual(self.ana.req("POST", "/api/auth/login", {"identifier": "ana", "pin": "73915864"})[0], 200, "unlocked by the admin")
        # PIN change
        self.assertEqual(self.ana.req("POST", "/api/auth/pin", {"old": "1", "new": "61829304"})[0], 403)
        self.assertEqual(self.ana.req("POST", "/api/auth/pin", {"old": "73915864", "new": "61829304"})[0], 200)
        self.assertEqual(Browser(self.base, "198.51.100.22").req("POST", "/api/auth/login", {"identifier": "ana", "pin": "61829304"})[0], 200)
        # CSRF: the session cookie alone does not make a POST work
        self.assertEqual(self.ana.req("POST", "/api/sandboxes", {"name": "x"}, {"X-Teambox": "0"})[0], 403)
        print("\n  sign-in: name (case/space-insensitive) + PIN, no Access header; logout = this session; sign out everywhere = all; idle "
              "241 min -> 401, 73 h -> 401; 5 wrong PINs -> 429 even with the right PIN, admin unlock; PIN change; CSRF header still needed")

    # ============================================================ optional profile (skip) + B. the vault
    def test_05_vault_and_optional_profile(self):
        if not hasattr(type(self), "ana"):
            self.test_04_signin_signout_timeouts()
        b = self.ana
        self.assertEqual(b.req("POST", "/api/setup/vault-later", {})[0], 409, "Lumi's key is set: no skipping the vault")
        s, st = b.req("GET", "/api/vault")
        self.assertEqual((st["vault"], st["lumi"]["kid"]), (None, self.lumi.kid))
        self.assertIn("Lumi", st["monitoring"])
        # refused wraps
        weak = {"kdf": {"name": "PBKDF2-SHA256", "iter": 1000, "salt": b64(os.urandom(16))}, "pw": {"iv": b64(os.urandom(12)), "wk": b64(os.urandom(48))}}
        self.assertEqual(b.req("POST", "/api/vault/init", weak)[0], 400, "too few PBKDF2 iterations")
        pw = "correct horse battery staple"
        vk = self.vault_init(b, pw)
        self.assertEqual(b.req("GET", "/api/me")[1]["setup_next"], "profile")
        rec = b.req("GET", "/api/vault")[1]["vault"]
        self.assertEqual(self.open_vk(rec, pw), vk, "the right password opens the vault key (any device: only the password is needed)")
        with self.assertRaises(InvalidTag):
            self.open_vk(rec, "wrong password")
        secret_note = "vault secret %s" % os.urandom(6).hex()
        env, ck = self.seal(vk, {"t": "note", "title": "plan", "text": secret_note})
        s, it = b.req("POST", "/api/vault/items", {"kind": "note", "env": env})
        self.assertEqual(s, 200, it)
        # refusals: plaintext, no Lumi key, an extra recipient, wrong Lumi kid
        plain = dict(env, ct=b64(b"this is not encrypted at all, plain words"))
        self.assertEqual(b.req("POST", "/api/vault/items", {"kind": "note", "env": plain})[0], 422)
        nolumi, _ = self.seal(vk, {"t": "note"}, lumi=False)
        self.assertEqual(b.req("POST", "/api/vault/items", {"kind": "note", "env": nolumi})[0], 422)
        other = K.Key()
        extra, _ = self.seal(vk, {"t": "note"}, extra=[{"to": "lumi2", "kid": other.kid, "wk": b64(K.wrap(os.urandom(32), other.spki))}])
        self.assertEqual(b.req("POST", "/api/vault/items", {"kind": "note", "env": extra})[0], 422)
        badkid, _ = self.seal(vk, {"t": "note"})
        badkid["keys"][1]["kid"] = other.kid
        self.assertEqual(b.req("POST", "/api/vault/items", {"kind": "note", "env": badkid})[0], 422)
        # a file in encrypted chunks
        data = os.urandom(300000)
        fenv, fck = self.seal(vk, {"t": "file", "name": "contract.pdf", "type": "application/pdf", "size": len(data)})
        s, fit = b.req("POST", "/api/vault/items", {"kind": "file", "env": fenv, "size": len(data)})
        self.assertEqual(s, 200, fit)
        fid = fit["item"]["id"]
        self.assertEqual(b.req("POST", "/api/vault/chunk", {"id": fid, "i": 0, "iv": b64(os.urandom(12)), "data": b64(b"%PDF-1.7 plain" + b"x" * 100)})[0], 422)
        for n, i in enumerate(range(0, len(data), 256 * 1024)):
            iv = os.urandom(12)
            self.assertEqual(b.req("POST", "/api/vault/chunk", {"id": fid, "i": n, "iv": b64(iv), "data": b64(AESGCM(fck).encrypt(iv, data[i:i + 256 * 1024], None))})[0], 200)
        self.assertEqual(b.req("POST", "/api/vault/finish", {"id": fid})[0], 200)
        got = b"".join(AESGCM(fck).decrypt(base64.b64decode(c["iv"]), base64.b64decode(c["data"]), None)
                       for c in (b.req("GET", "/api/vault/chunk?id=%s&i=%d" % (fid, i))[1] for i in range(2)))
        self.assertEqual(got, data)
        # the member re-opens with the password: vault key -> item key -> note
        it2 = b.req("GET", "/api/vault/item?id=" + it["item"]["id"])[1]["item"]
        vk2 = self.open_vk(b.req("GET", "/api/vault")[1]["vault"], pw)
        k = next(x for x in it2["env"]["keys"] if x["to"] == "vault")
        ck2 = AESGCM(vk2).decrypt(base64.b64decode(k["iv"]), base64.b64decode(k["wk"]), None)
        self.assertEqual(json.loads(AESGCM(ck2).decrypt(base64.b64decode(it2["env"]["iv"]), base64.b64decode(it2["env"]["ct"]), None))["text"], secret_note)
        # LUMI reads it through the export door, with her private key only
        r = urllib.request.urlopen(urllib.request.Request(self.base + "/export/vault?cursor=0", headers={"Authorization": "Bearer " + self.export_token}))
        ex = json.loads(r.read())
        note = next(x for x in ex["items"] if x["id"] == it["item"]["id"])
        self.assertEqual([k["to"] for k in note["env"]["keys"]], ["lumi"], "the export carries only Lumi's wrapped key")
        payload, _ = K.unseal(note["env"], self.lumi)
        self.assertEqual(payload["text"], secret_note, "Lumi's key decrypts the vault note")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(urllib.request.Request(self.base + "/export/vault?cursor=0", headers={"Authorization": "Bearer nope"}))
        self.assertEqual(cm.exception.code, 401, "the export door needs Lumi's export token")
        # other people: a different member never sees Ana's vault
        s, mst = self.marjus.req("GET", "/api/vault")
        self.assertEqual(mst["items"], [], "an admin's own vault is separate; the API has no way to read another member's vault")
        self.assertEqual(self.marjus.req("GET", "/api/vault/item?id=" + it["item"]["id"])[0], 404)
        # change the vault password: PIN required; old password stops working, items unchanged
        newpw = "a whole new passphrase 42"
        salt, iv = os.urandom(16), os.urandom(12)
        kek = hashlib.pbkdf2_hmac("sha256", newpw.encode(), salt, 600000, 32)
        body = {"kdf": {"name": "PBKDF2-SHA256", "iter": 600000, "salt": b64(salt)}, "pw": {"iv": b64(iv), "wk": b64(AESGCM(kek).encrypt(iv, vk, None))}}
        self.assertEqual(b.req("POST", "/api/vault/password", dict(body, pin="00000000"))[0], 403, "a stolen session alone can not swap it")
        self.assertEqual(b.req("POST", "/api/vault/password", dict(body, pin="61829304"))[0], 200)
        rec2 = b.req("GET", "/api/vault")[1]["vault"]
        self.assertEqual(self.open_vk(rec2, newpw), vk)
        with self.assertRaises(InvalidTag):
            self.open_vk(rec2, pw)
        # ciphertext only on disk
        vdir = os.path.join(self.cfg["data_dir"], "vault")
        blob = b""
        for dp, _ds, fs in os.walk(vdir):
            for f in fs:
                blob += open(os.path.join(dp, f), "rb").read()
        for p in (secret_note.encode(), b64(secret_note.encode()).encode(), pw.encode(), newpw.encode(), data[:64], b"contract.pdf"):
            self.assertNotIn(p, blob)
        # the OPTIONAL profile step: skip
        s, r = b.req("POST", "/api/setup/profile", {"skip": True})
        self.assertEqual((s, r["next"]), (200, "welcome"))
        s, r = self.marjus.req("POST", "/api/setup/profile", {"display_name": "Marjus G", "about": "runs the team", "contact": "via Lumi"})
        self.assertEqual(s, 200)
        me = self.marjus.req("GET", "/api/me")[1]
        self.assertEqual((me["display_name"], me["profile"]["about"]), ("Marjus G", "runs the team"))
        print("\n  vault: PBKDF2 600k wrap; right password opens, wrong -> InvalidTag; note + 300 KB file stored as ciphertext only; plain / "
              "no-Lumi / extra recipient / wrong kid refused (422); Lumi decrypts via /export/vault; admin 404 on Ana's item; password "
              "change needs the PIN, old password dead; profile step skipped -> next 'welcome' (Marjus filled his)")

    # ============================================================ C. Lumi's welcome + the first project
    def test_06_lumi_welcome(self):
        if not hasattr(type(self), "ana"):
            self.test_05_vault_and_optional_profile()
        b = self.ana
        if b.req("GET", "/api/me")[1]["setup_next"] != "welcome":
            self.test_05_vault_and_optional_profile()
        n0 = len(self.gw.calls)
        s, r = b.req("POST", "/api/welcome", {})
        self.assertEqual(s, 200, r)
        c = r["chat"]
        self.assertTrue(c["lumi"])
        self.assertEqual(c["title"], "Lumi - welcome")
        for must in (PURPOSE, "https://team.xseed.ing/#/projects", "https://team.xseed.ing/#/vault", "https://team.xseed.ing/#/m/messages",
                     "/#/project/", "sales-dashboard-shop", "Never ask for their PIN or their vault password", "monitoring notice"):
            self.assertIn(must, c["system_prompt"])
        self.assertEqual(c["messages"][0]["role"], "assistant")
        self.assertFalse(c["messages"][0].get("fallback"), "answered by the shared model through the gateway")
        calls = [x for x in self.gw.calls[n0:] if x["path"] == "/v1/chat/completions"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["account"], calls[0]["body"]["model"]), (ANA, "chat-llama"), "under Ana's own account and caps")
        self.assertEqual(calls[0]["body"]["messages"][0]["role"], "system")
        self.assertEqual(b.req("POST", "/api/welcome", {})[1]["chat"]["id"], c["id"], "made once")
        me = b.req("GET", "/api/me")[1]
        self.assertEqual((me["setup_next"], me["first_visit"], me["welcome_chat"]), (None, False, c["id"]))
        s, sb = b.req("GET", "/api/sandboxes/%s/sales-dashboard-shop" % me["id"])
        self.assertEqual((s, sb["label"]), (200, "Build the sales dashboard for our shop"))
        # the chat goes on with Lumi's system prompt
        s, r2 = b.req("POST", "/api/chats/%s/send" % c["id"], {"text": "where do I start?"})
        self.assertEqual(s, 200, r2)
        last = [x for x in self.gw.calls if x["path"] == "/v1/chat/completions"][-1]
        self.assertEqual(last["body"]["messages"][0]["content"], c["system_prompt"])
        # fallback: an account the gateway does not know -> the fixed text, which says why
        j = self.invite("Zed Unknown", purpose="test the mobile app")
        z = self.setup_person(j["code"], "zed", "48261937", ip="203.0.113.150")
        self.vault_init(z, "zed vault passphrase 1")
        z.req("POST", "/api/setup/profile", {"skip": True})
        s, rz = z.req("POST", "/api/welcome", {})
        m = rz["chat"]["messages"][0]
        self.assertTrue(m.get("fallback"))
        for must in ("test the mobile app", "not in the team policy", "#/project/", "mobile-app"):
            self.assertIn(must, m["content"])
        self.assertEqual(welcome.project_name("They're here to help with the Albanian translations"), "albanian-translations")
        self.assertEqual(welcome.project_name("!!!"), "first-project")
        tpl = os.path.join(ROOT, "config", "templates", "lumi-welcome.txt")
        self.assertTrue(os.path.exists(tpl))
        print("\n  welcome: Lumi chat via the gateway as %s (model chat-llama, system prompt from config/templates with the purpose + "
              "area links + first project link), made once, conversation keeps the prompt; unknown account -> fallback text with the reason; "
              "first project 'sales-dashboard-shop'" % ANA)

    # ============================================================ git tokens still work; pages; tour/how-it-works/prompt accurate
    def test_07_git_tokens_and_pages(self):
        if not hasattr(type(self), "ana"):
            self.test_04_signin_signout_timeouts()
        me = self.ana.req("GET", "/api/me")[1]
        s, t = self.ana.req("POST", "/api/tokens", {"label": "laptop"})
        self.assertEqual(s, 200, t)
        r = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", "http://x:%s@127.0.0.1:%d/git/%s/%s.git" % (t["token"], self.port, me["id"], me["first_project"]["name"])],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME=self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs/heads/main", r.stdout, "git tokens are the git login - no web session or Access needed")
        s, page = Browser(self.base).req("GET", "/join")
        self.assertEqual(s, 200)
        self.assertIn("/static/app.js", page, "/join serves the app, which shows the invite-code screen")
        self.assertIn("/static/vault.js", page)
        print("\n  git token clone (ls-remote) works without any web login; /join serves the app")

    def test_08_tour_howitworks_prompt(self):
        s, t = self.marjus.req("GET", "/api/tour")
        self.assertEqual(t["bottom_text"], EXACT_BOTTOM, "bottom text unchanged, exactly")
        self.assertEqual(tour.BOTTOM_TEXT, EXACT_BOTTOM)
        p = t["prompt"]
        for must in ("one-time 12-digit code that expires after 48 hours", "locks after 5 wrong tries", "PIN of at least 6 digits",
                     "HttpOnly, Secure and SameSite=Lax", "after 240 minutes without", "after 72 hours at most", "Sign out everywhere",
                     "Cloudflare Access: switched OFF", "Nobody - not Marjus, not Lumi - will ever ask me for my PIN or my vault password",
                     "13. The vault", "Lumi (the owner's AI) can read my vault", "covered by the monitoring notice", "14. Lumi's welcome",
                     "9. Share to feed: LIVE", "There is no automatic or scheduled publishing"):
            self.assertIn(must, p.replace("\n   ", " "), must)
        for gone in ("Cloudflare Access email login", "Tuesday", "weekly", "14:00"):
            self.assertNotIn(gone, p)
        vault_cp = next(c for c in t["checkpoints"] if c["id"] == "vault")
        self.assertIn("Lumi", vault_cp["text"])
        self.assertIn("monitoring notice", vault_cp["text"])
        s, page = Browser(self.base).req("GET", "/how-it-works")
        for must in ('id="signin"', "Accounts exist only by invitation", "12-digit code", "SameSite=Lax", 'id="vault"', "Lumi can read your vault",
                     "monitoring notice covers it", 'id="lumi"', 'id="sharing"', 'id="projects"',
                     "TEAM PROJECT SPACE - on the team computer, not your computer and not Marjus's computer"):
            self.assertIn(must, page)
        for gone in ("Tuesday", "you sign in with your email (a one-time code)", "Team feed (every"):
            self.assertNotIn(gone, page)
        ids = set(re.findall(r'id="([a-z-]+)"', page))
        self.assertTrue({c["id"] for c in t["checkpoints"]} <= ids, "every checkpoint's Details link has its section")
        js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf8").read()
        self.assertIn('"TEAM PROJECT SPACE - on the team computer, not your computer and not Marjus\'s computer"', js)
        self.assertIn('"#/projects", "Projects"', js)
        self.assertIn('v === "projects" || v === "sandboxes"', js, "old #/sandboxes addresses keep working")
        print("\n  tour/how-it-works/prompt: bottom text exact; sign-in, vault (Lumi can read it, monitoring notice), welcome, Share to feed "
              "described with the box's real numbers; no Access-email / Tuesday text; project banner + old URLs kept")

    # ============================================================ the real UI in headless Chromium: join -> PIN -> vault -> skip -> Lumi
    def test_90_browser_join_vault_welcome(self):
        chromium, node = shutil.which("chromium") or shutil.which("chromium-browser"), shutil.which("node")
        if not chromium or not node:
            self.skipTest("chromium/node not installed")
        j = self.invite("Web Ana", purpose="write the onboarding guide")
        note = "browser vault note %s" % os.urandom(5).hex()
        pw = "seven blue lanterns over tirana"
        cdp = free_port()
        steps = [{"size": [390, 900]}, {"goto": self.base + "/join"}, {"waitFor": "#joinCode"}, {"type": ["#joinCode", j["code"]]},
                 {"click": "form button[type=submit]"}, {"waitFor": "#setupPin"}, {"type": ["#setupId", "web-ana"]},
                 {"type": ["#setupPin", "58203917"]}, {"type": ["#setupPin2", "58203917"]}, {"click": "form button[type=submit]"},
                 {"waitFor": "#vpw1"}, {"type": ["#vpw1", pw]}, {"type": ["#vpw2", pw]}, {"click": "form button[type=submit]"},
                 {"waitFor": "#profileSkip"}, {"click": "#profileSkip"}, {"waitFor": ".msg.assistant"},
                 {"eval": "JSON.stringify({hash: location.hash, lumi: document.querySelector('.msg.assistant').textContent.slice(0, 200), title: document.querySelector('h1').textContent})"},
                 {"eval": "location.hash = '#/vault'"}, {"waitFor": "#noteTitle"}, {"type": ["#noteTitle", "plan"]}, {"type": ["#noteText", note]},
                 {"click": "form button[type=submit]"}, {"waitFor": "pre.preview"},
                 {"eval": "document.querySelector('pre.preview').textContent"},
                 {"goto": self.base + "/#/vault"}, {"waitFor": "#vaultPass"}, {"type": ["#vaultPass", "not my password"]},
                 {"click": "form button[type=submit]"}, {"waitFor": ".err"}, {"eval": "document.querySelector('.err').textContent"},
                 {"type": ["#vaultPass", pw]}, {"click": "form button[type=submit]"}, {"waitFor": "pre.preview"},
                 {"eval": "document.querySelector('pre.preview').textContent"},
                 {"eval": "document.cookie.indexOf('tb_session') < 0 ? 'session cookie hidden from JS (HttpOnly)' : 'VISIBLE'"}]
        sf = os.path.join(self.tmp, "steps.json")
        with open(sf, "w") as fh:
            json.dump(steps, fh)
        ud = tempfile.mkdtemp(dir=self.tmp, prefix="chr-")
        p = subprocess.Popen([chromium, "--headless=new", "--no-first-run", "--disable-gpu", "--user-data-dir=" + ud,
                              "--remote-debugging-port=%d" % cdp, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            r = subprocess.run([node, os.path.join(HERE, "cdp.mjs"), str(cdp), sf], capture_output=True, text=True, timeout=300)
        finally:
            p.kill()
            p.wait()
        self.assertEqual(r.stderr.strip(), "", "every screen appeared: " + r.stderr)
        out = [json.loads(x) for x in r.stdout.splitlines() if x and not x.startswith("shot ")]
        first = json.loads(out[0])
        self.assertTrue(first["hash"].startswith("#/chat/"), first)
        self.assertEqual(first["title"], "Lumi - welcome")
        self.assertEqual(out[2], note, "the browser encrypted + decrypted its own note")
        self.assertIn("wrong vault password", out[3])
        self.assertEqual(out[4], note, "after a reload only the password opens it")
        self.assertEqual(out[5], "session cookie hidden from JS (HttpOnly)")
        aid = "web-ana@team.xseed.ing"
        slug = self.app.store.slug_for(aid, create=False)
        items = self.app.vault.items(slug)
        it = self.app.vault.item(slug, items[0]["id"])
        payload, _ = K.unseal(dict(it["env"], keys=[k for k in it["env"]["keys"] if k["to"] == "lumi"]), self.lumi)
        self.assertEqual(payload["text"], note, "Lumi's key (Python, BESNIK's side) opens what the browser's WebCrypto sealed")
        raw = open(self.app.vault.p(slug, "vault.json")).read() + json.dumps(it)
        self.assertNotIn(note, raw)
        self.assertNotIn(pw, raw)
        rec = json.loads(open(self.app.vault.p(slug, "vault.json")).read())
        self.assertEqual(rec["kdf"]["iter"], 600000)
        self.assertEqual(self.open_vk(rec, pw).__len__(), 32, "the browser's PBKDF2 wrap opens in Python with the same password")
        print("\n  headless Chromium: /join -> code -> sign-in name + PIN -> vault password -> Skip -> Lumi's welcome chat; vault note "
              "sealed by WebCrypto, Lumi decrypts it in Python; reload -> wrong password refused, right one opens; tb_session not visible to JS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
