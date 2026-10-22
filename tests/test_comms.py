"""Messages & calls tests. Run on Linux:   python3 -m unittest -v tests.test_comms
Starts the REAL team box (in-process, temp data dir, dev mode) with a TEST Lumi key made here. The browser's scheme is re-done in
Python (tests/comms_crypto.py, same bytes as WebCrypto); test_90 also runs the real static/comms.js in headless Chromium (fake
camera/mic) when chromium is installed. BESNIK's lumi_comms_review.py is used from /mnt/d/Atlas/app when it is reachable."""
import base64
import hashlib
import json
import os
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
from teambox import config as C  # noqa: E402
from teambox import comms as CM  # noqa: E402
from teambox import tour  # noqa: E402
from teambox.modules import messages  # noqa: E402
from teambox.server import make_server  # noqa: E402

A, B, CC, D, ADMIN = "ana@test.example", "ben@test.example", "cara@test.example", "dev@test.example", "marjus@test.example"
REVIEW_DIRS = [os.environ.get("LUMI_APP_DIR", ""), "/mnt/d/Atlas/app", os.path.join(ROOT, "..", "app")]
PLAINTEXTS = []          # every plaintext the tests send: test_99 proves none of it is on the team box's disk


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def rd(p):
    with open(p, encoding="utf8") as fh:
        return fh.read()


def secret(label):
    s = "%s %s" % (label, os.urandom(6).hex())
    PLAINTEXTS.append(s)
    return s


class CommsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="teambox-comms-")
        cls.lumi = K.Key()
        cls.export_token = "lce_test_" + os.urandom(16).hex()
        cls.port = free_port()
        cfgp = os.path.join(cls.tmp, "config.json")
        with open(cfgp, "w") as fh:
            json.dump({"port": cls.port, "data_dir": os.path.join(cls.tmp, "data"), "public_url": "http://127.0.0.1:%d" % cls.port,
                       "admin_emails": [ADMIN], "dev_mode": True, "gateway": {"url": "http://127.0.0.1:9", "secret_file": ""},
                       "comms": {"lumi_public_key": cls.lumi.spki, "lumi_kid": cls.lumi.kid,
                                 "export_token_sha256": hashlib.sha256(cls.export_token.encode()).hexdigest()}}, fh)
        os.environ["TEAMBOX_ALLOW_DEV"] = "1"
        cls.cfg = C.load(cfgp)
        cls.app, cls.srv = make_server(cls.cfg)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        cls.keys = {}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def api(self, method, path, who=None, body=None, headers=None):
        h = dict(headers or {})
        if who:
            h["X-Dev-Email"] = who
        data = None
        if method == "POST":
            h["X-Teambox"] = "1"
            h["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        r = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())
        except urllib.error.HTTPError as e:
            raw = e.read()
            e.close()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode()

    M = "/api/m/messages"

    def slug(self, who):
        return self.api("GET", "/api/me", who)[1]["id"]

    def ready(self, who):
        """Sign in, accept the notice, register a (Python) device key. -> (slug, Key)."""
        s = self.slug(who)
        if who not in self.keys:
            st = self.api("GET", self.M + "/state", who)[1]
            if not st["accepted"]:
                self.assertEqual(self.api("POST", self.M + "/notice/accept", who, {"agree": True, "version": st["version"]})[0], 200)
            k = K.Key()
            code, j = self.api("POST", self.M + "/keys", who, {"spki": k.spki, "label": "python test key"})
            self.assertEqual(code, 200, j)
            self.keys[who] = k
        return s, self.keys[who]

    def recips(self, tid, who):
        th = self.api("GET", self.M + "/thread?id=" + tid, who)[1]
        out = [("lumi", th["lumi"]["kid"], th["lumi"]["spki"])]
        for m, ks in th["keys"].items():
            out += [(m, k["kid"], k["spki"]) for k in ks]
        return out

    def thread(self, who, *others):
        code, j = self.api("POST", self.M + "/threads", who, {"members": [self.slug(o) for o in others]})
        self.assertEqual(code, 200, j)
        return j["thread"]["id"]

    def send(self, who, tid, obj, recips=None, att=None):
        env, ck = K.seal(obj, recips or self.recips(tid, who))
        return self.api("POST", self.M + "/send", who, {"thread": tid, "env": env, "att": att or []}), ck

    # ============================================================ 1. the monitoring notice gate
    def test_01_notice_gate_blocks_until_accepted(self):
        s = self.slug(D)
        st = self.api("GET", self.M + "/state", D)[1]
        self.assertFalse(st["accepted"])
        self.assertEqual(st["notice"], messages.NOTICE, "the gate shows Marjus's notice (fixed typos), exactly")
        self.assertEqual(st["version"], messages.NOTICE_SHA256)
        k = K.Key()
        for meth, path, body in (("GET", "/threads", None), ("GET", "/people", None), ("POST", "/keys", {"spki": k.spki}),
                                 ("POST", "/threads", {"members": []}), ("POST", "/send", {}), ("POST", "/call/start", {}),
                                 ("GET", "/poll", None), ("POST", "/blob/start", {"kind": "att"})):
            code, j = self.api(meth, self.M + path, D, body)
            self.assertEqual(code, 403, "%s %s must be blocked before the notice is accepted: %s" % (meth, path, j))
            self.assertIn("monitoring notice", j["error"])
        self.assertEqual(self.api("POST", self.M + "/notice/accept", D, {"version": st["version"]})[0], 400, "agree:true is required")
        self.assertEqual(self.api("POST", self.M + "/notice/accept", D, {"agree": True, "version": "0" * 64})[0], 400,
                         "accepting a different (old) version does not count")
        code, j = self.api("POST", self.M + "/notice/accept", D, {"agree": True, "version": st["version"]})
        self.assertEqual(code, 200, j)
        rec = json.loads(rd(os.path.join(self.cfg["data_dir"], "comms", "acceptances.json")))[s]
        self.assertEqual((rec["email"], rec["version"], rec["label"]), (D, messages.NOTICE_SHA256, "2026-10-21"))
        self.assertTrue(rec["at"])
        hist = rd(os.path.join(self.cfg["data_dir"], "comms", "acceptances.jsonl"))
        self.assertIn(D, hist)
        self.assertEqual(self.api("GET", self.M + "/threads", D)[0], 200, "after accepting, the area opens")
        # the notice changes -> everyone is asked again
        old = messages.NOTICE_SHA256
        messages.NOTICE_SHA256 = hashlib.sha256(b"a changed notice").hexdigest()
        try:
            st2 = self.api("GET", self.M + "/state", D)[1]
            self.assertFalse(st2["accepted"])
            self.assertTrue(st2["previous_version_accepted"])
            self.assertEqual(self.api("GET", self.M + "/threads", D)[0], 403, "a new notice version blocks again until accepted")
        finally:
            messages.NOTICE_SHA256 = old
        self.assertEqual(self.api("GET", self.M + "/threads", D)[0], 200)
        js = rd(os.path.join(ROOT, "static", "comms.js"))
        self.assertIn("I understand and agree", js)
        self.assertIn("comms-reminder", js, "the permanent reminder line is in the UI")
        print("\n  notice gate: 8 routes 403 before accepting; stored %s / %s / %s...; new notice version -> blocked again"
              % (rec["email"], rec["at"], rec["version"][:12]))

    # ============================================================ 2. keys
    def test_10_key_registration(self):
        self.ready(A)
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        small = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        code, j = self.api("POST", self.M + "/keys", A, {"spki": base64.b64encode(small).decode()})
        self.assertEqual(code, 400)
        self.assertIn("3072", j["error"])
        self.assertEqual(self.api("POST", self.M + "/keys", A, {"spki": base64.b64encode(b"\x30\x03\x02\x01\x01" * 50).decode()})[0], 400)
        self.assertEqual(self.api("POST", self.M + "/keys", A, {"spki": self.lumi.spki})[0], 400, "nobody can register Lumi's key as theirs")
        self.ready(B)
        code, j = self.api("POST", self.M + "/keys", B, {"spki": self.keys[A].spki})
        self.assertEqual(code, 409, "a key already registered to another member is refused")
        mine = self.api("GET", self.M + "/state", A)[1]["my_keys"]
        self.assertEqual(mine[0]["kid"], self.keys[A].kid)
        self.assertNotIn("priv", json.dumps(mine))
        self.assertEqual(CM.kid_of(self.keys[A].spki_der), self.keys[A].kid)

    # ============================================================ 3. messages: ciphertext only; Lumi reads; outsiders can not
    def test_11_message_lumi_reads_outsider_cannot(self):
        a, ka = self.ready(A)
        b, kb = self.ready(B)
        c, kc = self.ready(CC)
        tid = self.thread(A, B)
        text = secret("Ana to Ben: the plan for the parser")
        (code, j), ck = self.send(A, tid, {"t": text, "att": []})
        self.assertEqual(code, 200, j)
        mid = j["message"]["id"]
        stored = [json.loads(x) for x in rd(os.path.join(self.cfg["data_dir"], "comms", "messages", tid + ".jsonl")).splitlines()]
        env = [m for m in stored if m["id"] == mid][0]["env"]
        self.assertEqual({k["to"] for k in env["keys"]}, {a, b, "lumi"}, "wrapped for Ana, Ben and Lumi - nobody else")
        self.assertEqual(K.unseal(env, self.lumi)[0]["t"], text, "LUMI's private key decrypts it")
        self.assertEqual(K.unseal(env, kb)[0]["t"], text, "Ben (in the thread) decrypts it")
        with self.assertRaises(KeyError):
            K.unseal(env, kc)                                                   # Cara's key is not among the wrapped keys
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        for k in env["keys"]:                                                   # and none of the wrapped keys opens with Cara's key
            with self.assertRaises(ValueError):
                kc.priv.decrypt(K.unb64(k["wk"]), K.OAEP)
        with self.assertRaises(InvalidTag):
            AESGCM(os.urandom(32)).decrypt(K.unb64(env["iv"]), K.unb64(env["ct"]), None)
        self.assertEqual(self.api("GET", self.M + "/thread?id=" + tid, CC)[0], 404, "Cara (not in the thread) can not even fetch it")
        self.assertEqual(self.api("GET", self.M + "/thread?id=" + tid, ADMIN)[0], 403, "admin has not accepted -> gated")
        self.ready(ADMIN)
        self.assertEqual(self.api("GET", self.M + "/thread?id=" + tid, ADMIN)[0], 404, "an admin who is not in the thread can not fetch it")
        ov = self.api("GET", self.M + "/admin", ADMIN)[1]
        self.assertIn(tid, [t["id"] for t in ov["threads"]], "admins see METADATA (who, when, how many)")
        self.assertNotIn(env["ct"], json.dumps(ov), "...but not even the ciphertext")
        self.assertEqual(self.api("GET", self.M + "/admin", A)[0], 403)
        got = self.api("GET", self.M + "/thread?id=" + tid, B)[1]["messages"]
        self.assertEqual(K.unseal(got[-1]["env"], kb)[0]["t"], text, "Ben reads his own thread through the API")
        type(self).t_ab = tid
        print("\n  message %s: stored as ciphertext; Lumi + Ben decrypt it; Cara's key opens none of the 3 wrapped keys; Cara/admin get 404" % mid)

    def test_12_server_refuses_what_is_not_proper_ciphertext(self):
        a, ka = self.ready(A)
        b, kb = self.ready(B)
        c, kc = self.ready(CC)
        tid = self.thread(A, B)
        good = self.recips(tid, A)
        cases = []
        env, _ = K.seal({"t": "x"}, good)
        cases.append(("plaintext as ct", dict(env, ct=base64.b64encode(secret("hello this is plain text, not encrypted").encode()).decode()), 422))
        cases.append(("JSON as ct", dict(env, ct=base64.b64encode(json.dumps({"t": secret("plain json payload")}).encode()).decode()), 422))
        cases.append(("no Lumi key", K.seal({"t": "x"}, [r for r in good if r[0] != "lumi"])[0], 422))
        cases.append(("Ben's key missing", K.seal({"t": "x"}, [r for r in good if r[0] != b])[0], 422))
        cases.append(("wrapped for Cara (not in thread)", K.seal({"t": "x"}, good + [(c, kc.kid, kc.spki)])[0], 422))
        other_lumi = K.Key()
        cases.append(("wrong Lumi key", K.seal({"t": "x"}, [r for r in good if r[0] != "lumi"] + [("lumi", other_lumi.kid, other_lumi.spki)])[0], 422))
        cases.append(("short wrapped key", dict(env, keys=[dict(k, wk=base64.b64encode(b"x" * 100).decode()) for k in env["keys"]]), 400))
        cases.append(("bad iv", dict(env, iv=base64.b64encode(b"123").decode()), 400))
        cases.append(("not an envelope", {"text": "hi"}, 400))
        for name, e, want in cases:
            code, j = self.api("POST", self.M + "/send", A, {"thread": tid, "env": e})
            self.assertEqual(code, want, "%s -> %s %s" % (name, code, j))
        self.assertEqual(self.api("POST", self.M + "/send", A, {"thread": tid, "env": env})[0], 200, "the proper envelope is accepted")
        self.assertEqual(self.api("POST", self.M + "/send", CC, {"thread": tid, "env": env})[0], 404, "a non-member can not post")
        print("\n  refused: " + "; ".join("%s %d" % (n, w) for n, _, w in cases))

    def test_13_attachments_encrypted_chunks(self):
        a, ka = self.ready(A)
        b, kb = self.ready(B)
        c, kc = self.ready(CC)
        tid = self.thread(A, B)
        ck = K.AESGCM.generate_key(256)
        body = (secret("attachment text") + "\n").encode() * 30000          # ~1 MB -> 3 chunks of 400 KB
        code, j = self.api("POST", self.M + "/blob/start", A, {"kind": "att", "thread": tid})
        self.assertEqual(code, 200, j)
        bid = j["blob"]["id"]
        code, j = self.api("POST", self.M + "/blob/chunk", A, {"id": bid, "i": 0, "iv": K.b64(os.urandom(12)),
                                                               "data": K.b64(b"\x89PNG\r\n\x1a\n" + os.urandom(2000))})
        self.assertEqual(code, 422, "a plain PNG is refused")
        code, j = self.api("POST", self.M + "/blob/chunk", A, {"id": bid, "i": 0, "iv": K.b64(os.urandom(12)), "data": K.b64(body[:3000])})
        self.assertEqual(code, 422, "plain text is refused")
        ivs = []
        for i in range(0, len(body), 400 * 1024):
            iv, data = K.chunk(ck, body[i:i + 400 * 1024])
            ivs.append(iv)
            code, j = self.api("POST", self.M + "/blob/chunk", A, {"id": bid, "i": len(ivs) - 1, "iv": iv, "data": data})
            self.assertEqual(code, 200, j)
        iv2, data2 = K.chunk(ck, b"more")
        self.assertEqual(self.api("POST", self.M + "/blob/chunk", A, {"id": bid, "i": len(ivs), "iv": ivs[0], "data": data2})[0], 422,
                         "an IV is never accepted twice")
        self.assertEqual(self.api("POST", self.M + "/blob/finish", B, {"id": bid})[0], 404, "not Ben's upload")
        self.assertEqual(self.api("POST", self.M + "/blob/finish", A, {"id": bid})[0], 200)
        env = K.seal({"t": "file", "att": [{"blob": bid, "name": "notes.txt", "type": "text/plain", "size": len(body)}]}, self.recips(tid, A), ck)[0]
        self.assertEqual(self.api("POST", self.M + "/send", A, {"thread": tid, "env": env, "att": [bid]})[0], 200)
        code, j = self.api("POST", self.M + "/send", A, {"thread": tid, "env": env, "att": [bid]})
        self.assertEqual(code, 400, "an attachment belongs to one message only")
        self.assertEqual(K.unseal(env, self.lumi)[0]["att"][0]["name"], "notes.txt", "the file name is inside the ciphertext")
        parts = []
        for i in range(len(ivs)):
            code, ch = self.api("GET", self.M + "/blob?id=%s&i=%d" % (bid, i), B)
            self.assertEqual(code, 200, ch)
            parts.append(K.unchunk(ck, ch["iv"], ch["data"]))
        self.assertEqual(b"".join(parts), body, "Ben gets the chunks and decrypts the file in his browser")
        self.assertEqual(self.api("GET", self.M + "/blob?id=%s&i=0" % bid, CC)[0], 404, "Cara can not fetch it")
        print("\n  attachment: %d KB in %d encrypted chunks; PNG/plain chunks refused (422); IV reuse refused; Cara 404" % (len(body) // 1024, len(ivs)))

    # ============================================================ 4. calls: signalling + the recording for Lumi
    def test_20_call_signalling_and_recording(self):
        a, ka = self.ready(A)
        b, kb = self.ready(B)
        c, kc = self.ready(CC)
        tid = self.thread(A, B)
        seq_b = self.api("GET", self.M + "/poll?since=0", B)[1]["seq"]
        code, j = self.api("POST", self.M + "/call/start", A, {"thread": tid, "video": True})
        self.assertEqual(code, 200, j)
        cid = j["call"]["id"]
        sig = self.api("GET", self.M + "/poll?since=%d&wait=2" % seq_b, B)[1]["signals"]
        self.assertTrue(any(s["type"] == "ring" and s["from"] == a for s in sig), "Ben's browser is told it rings: %s" % sig)
        self.assertEqual(self.api("POST", self.M + "/call/join", CC, {"call": cid})[0], 404, "Cara is not in this conversation")
        code, j = self.api("POST", self.M + "/call/join", B, {"call": cid})
        self.assertEqual((code, j["connect_to"]), (200, [a]), "the newcomer offers to everyone already in")
        seq_a = 0
        self.api("POST", self.M + "/call/signal", B, {"call": cid, "to": a, "type": "offer", "data": {"type": "offer", "sdp": "v=0 fake"}})
        sig = self.api("GET", self.M + "/poll?since=%d&wait=2" % seq_a, A)[1]["signals"]
        self.assertTrue(any(s["type"] == "offer" and s["from"] == b for s in sig))
        self.assertEqual(self.api("POST", self.M + "/call/signal", B, {"call": cid, "to": c, "type": "offer", "data": {}})[0], 400)
        # the recording: only the caller; only for Lumi; only ciphertext
        lumi_r = ("lumi", self.lumi.kid, self.lumi.spki)
        ck = K.AESGCM.generate_key(256)
        env_lumi = {"v": 1, "alg": K.ALG, "keys": [{"to": "lumi", "kid": self.lumi.kid, "wk": K.b64(K.wrap(ck, self.lumi.spki))}]}
        self.assertEqual(self.api("POST", self.M + "/blob/start", B, {"kind": "rec", "call": cid, "env": env_lumi})[0], 403,
                         "only the caller's browser records")
        env_too = dict(env_lumi, keys=env_lumi["keys"] + [{"to": a, "kid": ka.kid, "wk": K.b64(K.wrap(ck, ka.spki))}])
        code, j = self.api("POST", self.M + "/blob/start", A, {"kind": "rec", "call": cid, "env": env_too})
        self.assertEqual(code, 422, "a recording wrapped for anyone but Lumi is refused: %s" % j)
        self.assertIn("ONLY for Lumi", j["error"])
        code, j = self.api("POST", self.M + "/blob/start", A, {"kind": "rec", "call": cid, "env": env_lumi})
        self.assertEqual(code, 200, j)
        rid = j["blob"]["id"]
        webm = b"\x1a\x45\xdf\xa3" + os.urandom(3000)
        code, j = self.api("POST", self.M + "/blob/chunk", A, {"id": rid, "i": 0, "iv": K.b64(os.urandom(12)), "data": K.b64(webm)})
        self.assertEqual(code, 422, "a plain WebM recording chunk is refused")
        media = webm + os.urandom(20000)
        pieces = [media[i:i + 8000] for i in range(0, len(media), 8000)]
        for i, p in enumerate(pieces):
            iv, data = K.chunk(ck, p)
            self.assertEqual(self.api("POST", self.M + "/blob/chunk", A, {"id": rid, "i": i, "iv": iv, "data": data})[0], 200)
        st = self.api("GET", self.M + "/call?id=" + cid, B)[1]["call"]
        self.assertEqual(st["recording"]["chunks"], len(pieces), "everyone in the call sees how many encrypted chunks arrived")
        self.assertEqual(self.api("GET", self.M + "/blob?id=%s&i=0" % rid, A)[0], 404, "nobody on the team box downloads a recording - not even the caller")
        self.assertEqual(self.api("GET", self.M + "/blob?id=%s&i=0" % rid, B)[0], 404)
        code, j = self.api("POST", self.M + "/call/leave", A, {"call": cid, "end": True})
        self.assertTrue(j["call"]["ended"])
        self.assertTrue(j["call"]["recording"]["done"], "ending the call finishes the recording (even if the caller's tab did not)")
        self.assertEqual(self.api("POST", self.M + "/call/signal", B, {"call": cid, "to": a, "type": "ice", "data": {}})[0], 409)
        # Lumi decrypts the recording from the export
        exp = self.export(0)
        rec = [x for x in exp["items"] if x["type"] == "rec" and x["id"] == rid][0]
        lck = K.unwrap(rec["env"], self.lumi)
        got = b"".join(K.unchunk(lck, ch["iv"], ch["data"]) for ch in (self.export_blob(rid, i) for i in range(rec["chunks"])))
        self.assertEqual(got, media, "Lumi's key opens the whole recording; it starts with the WebM header")
        with self.assertRaises(KeyError):
            K.unwrap(rec["env"], ka)
        # group call limit (mesh)
        tid5 = self.thread(A, B, CC, D, ADMIN)
        code, j = self.api("POST", self.M + "/call/start", A, {"thread": tid5})
        self.assertEqual(code, 409)
        self.assertIn("SFU", j["error"])
        print("\n  call %s: ring -> join -> offer relayed; recording %d chunks, Lumi-only, plain WebM refused, members 404, Lumi decrypts %d bytes; 5-person call: %s"
              % (cid, len(pieces), len(got), j["error"][:60]))

    def test_21_ice_turn_credentials(self):
        self.ready(A)
        sec = os.path.join(self.tmp, "turn.secret")
        with open(sec, "w") as fh:
            fh.write("turnsecret")
        self.app.cfg["comms"]["turn"] = {"urls": ["turn:turn.example:3478"], "secret_file": sec, "ttl_s": 600}
        try:
            ice = self.app.comms.ice("ana-test-example")
        finally:
            self.app.cfg["comms"].pop("turn")
        t = ice[-1]
        exp, user = t["username"].split(":")
        self.assertEqual(user, "ana-test-example")
        import hmac
        self.assertEqual(t["credential"], base64.b64encode(hmac.new(b"turnsecret", t["username"].encode(), hashlib.sha1).digest()).decode())
        self.assertTrue(int(exp) > time.time())

    # ============================================================ 5. the export door for BESNIK + the review pipeline
    def export(self, cursor, token=None):
        r = urllib.request.Request(self.base + "/export/comms?cursor=%d" % cursor, headers={"Authorization": "Bearer " + (token or self.export_token)})
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read())

    def export_blob(self, bid, i):
        r = urllib.request.Request(self.base + "/export/comms/blob?id=%s&i=%d" % (bid, i), headers={"Authorization": "Bearer " + self.export_token})
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read())

    def test_30_export_needs_the_token_and_gives_ciphertext_only(self):
        for hdr in ({}, {"Authorization": "Bearer wrong"}, {"X-Dev-Email": ADMIN}):
            try:
                urllib.request.urlopen(urllib.request.Request(self.base + "/export/comms?cursor=0", headers=hdr), timeout=10)
                self.fail("export without the token must fail")
            except urllib.error.HTTPError as e:
                e.close()
                self.assertEqual(e.code, 401)
        a, ka = self.ready(A)
        tid = self.thread(A, B)
        text = secret("export check")
        self.send(A, tid, {"t": text})
        exp = self.export(0)
        msgs = [x for x in exp["items"] if x["type"] == "msg"]
        self.assertTrue(msgs)
        self.assertTrue(all({k["to"] for k in x["env"]["keys"]} == {"lumi"} for x in msgs), "the export carries only Lumi's wrapped key")
        self.assertNotIn(text, json.dumps(exp))
        self.assertEqual(K.unseal(msgs[-1]["env"], self.lumi)[0]["t"], text)
        self.assertEqual(exp["lumi_kid"], self.lumi.kid)
        later = self.export(exp["cursor"])
        self.assertEqual(later["items"], [], "the cursor works")

    def review_module(self):
        for d in REVIEW_DIRS:
            if d and os.path.exists(os.path.join(d, "lumi_comms_review.py")):
                sys.path.insert(0, d)
                import lumi_comms_review
                return lumi_comms_review
        return None

    def test_31_review_pipeline_end_to_end(self):
        R = self.review_module()
        if not R:
            self.skipTest("lumi_comms_review.py not reachable from here")
        kdir = os.path.join(self.tmp, "lumi-key-dir")
        os.makedirs(kdir, exist_ok=True)
        with open(os.path.join(kdir, "lumi-comms-private.pem"), "wb") as fh:
            fh.write(self.lumi.pem())
        with open(os.path.join(kdir, "export.token"), "w") as fh:
            fh.write(self.export_token)
        self.ready(A)
        self.ready(B)
        tid = self.thread(A, B)
        start = self.export(0)["cursor"]
        st = os.path.join(kdir, "review-state.json")
        with open(st, "w") as fh:
            json.dump({"cursor": start, "counts": {}}, fh)
        self.send(A, tid, {"t": secret("I openly want my idea to win the vote, please look at it")})
        self.send(B, tid, {"t": secret("let's secretly agree to vote against Cara's idea, don't tell the others")})
        self.send(A, tid, {"t": secret("the secret of fast tests is a warm cache")})
        inbox = os.path.join(self.tmp, "concerns.jsonl")
        esc, state = R.run_once(R.BoxClient(self.base, kdir), R.LumiKey(kdir), R.make_ai("fake-strict"), R.make_ai("fake-careful"), inbox, st,
                                log=lambda *_: None)
        self.assertEqual(len(esc), 1, "only the message BOTH AIs flag reaches the admin inbox")
        self.assertIn("secretly agree", esc[0]["flagged_text"])
        self.assertEqual(state["counts"].get("ai1_concerns"), 2)
        self.assertEqual(state["counts"].get("ai2_disagreed"), 1)
        self.assertIn("ben@test.example", esc[0]["where"]["from"])
        print("\n  review pipeline vs the real export: 3 new messages -> AI#1 flagged 2 -> AI#2 agreed on 1 -> inbox: %s" % esc[0]["summary"][:140])

    # ============================================================ 6. tour + how-it-works claims
    def test_40_tour_and_public_claims(self):
        t = self.api("GET", "/api/public/tour")[1]
        cp = [c for c in t["checkpoints"] if c["id"] == "messages"][0]
        for w in ("encrypted in your browser", "Lumi", "can not read", "who talks to whom", "Recording for Lumi", "notice"):
            self.assertIn(w, cp["text"])
        p = t["prompt"]
        self.assertIn("10. Messages, calls and video calls", p)
        claim10 = p[p.index("10. Messages"):p.index("11. ")]
        for w in ("LIVE", "RSA-OAEP-3072", "AES-256-GCM", "Lumi", "can NOT decrypt", "metadata", "PARTLY", "modified", "SFU", "safety code"):
            self.assertIn(w, claim10)
        self.assertNotIn("NOT LIVE YET (announced", claim10)
        page = self.api("GET", "/how-it-works")[1]
        for w in ("id=\"messages\"", "Lumi can read", "can not decrypt", "visible to the team box", "Recording for Lumi", "SFU", "two different AIs"):
            self.assertIn(w, page)
        self.assertEqual(self.api("GET", "/api/modules", A)[1]["modules"][-1]["status"], "live")

    # ============================================================ 7. headless Chromium: the real comms.js
    def test_90_browser_selftest_headless_chromium(self):
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        if not chromium:
            self.skipTest("chromium not installed")
        b, kb = self.ready(B)
        ana_web = "web@test.example"
        st = self.api("GET", self.M + "/state", ana_web)[1]
        self.api("POST", self.M + "/notice/accept", ana_web, {"agree": True, "version": st["version"]})
        a = self.slug(ana_web)
        ud = tempfile.mkdtemp(dir=self.tmp, prefix="chr-")
        url = "%s/dev/login?email=%s&next=%s" % (self.base, ana_web, urllib.request.quote("/#/m/messages/selftest/" + b, safe=""))
        p = subprocess.Popen([chromium, "--headless=new", "--no-first-run", "--disable-gpu", "--user-data-dir=" + ud, "--remote-debugging-port=0",
                              "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                              url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result, t0 = None, time.time()
        try:
            while time.time() - t0 < 120 and not result:
                time.sleep(1)
                for th in self.app.comms.threads_of(a):
                    for m in self.app.comms._messages(th["id"]):
                        try:
                            pl = K.unseal(m["env"], self.lumi)[0]          # read with LUMI's key, like BESNIK would
                        except Exception:  # noqa: BLE001
                            continue
                        if str(pl.get("t", "")).startswith("SELFTEST-RESULT "):
                            result = json.loads(pl["t"][len("SELFTEST-RESULT "):])
                            result["_thread"] = th["id"]
        finally:
            p.kill()
            p.wait()
        self.assertTrue(result, "the browser self test reported back (encrypted) within 120 s")
        PLAINTEXTS.append(result.get("sent") or "-")
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result["roundtrip_text"] and result["roundtrip_attachment"], result)
        self.assertTrue(result["plaintext_refused"], "the server refused a plaintext 'envelope' from the browser")
        msgs = self.app.comms._messages(result["_thread"])
        first = [m for m in msgs if m["att"]][0]
        pl, ck = K.unseal(first["env"], self.lumi)
        self.assertEqual(pl["t"], result["sent"], "Lumi (Python, BESNIK's scheme) decrypts what the browser (WebCrypto) encrypted")
        self.assertEqual(K.unseal(first["env"], kb)[0]["t"], result["sent"], "and so does Ben's key")
        self.assertEqual({k["to"] for k in first["env"]["keys"]}, {a, b, "lumi"})
        bkid = self.api("GET", self.M + "/state", ana_web)[1]["my_keys"][0]["kid"]
        self.assertEqual(bkid, result["key_kid"], "the browser registered its own key (its private half never left it)")
        att = self.app.comms.blob_meta(first["att"][0])
        body = b"".join(K.unchunk(ck, ch["iv"], ch["data"]) for ch in (self.app.comms.blob_chunk_get(att["id"], i) for i in range(len(att["chunks"]))))
        self.assertTrue(body.startswith(b"attachment body "))
        rec = self.app.comms.blob_meta(result["recording_blob"])
        self.assertEqual([k["to"] for k in rec["env"]["keys"]], ["lumi"])
        rck = K.unwrap(rec["env"], self.lumi)
        media = b"".join(K.unchunk(rck, ch["iv"], ch["data"]) for ch in (self.app.comms.blob_chunk_get(rec["id"], i) for i in range(len(rec["chunks"]))))
        self.assertTrue(media.startswith(b"\x1a\x45\xdf\xa3"), "Lumi's key opens the browser's call recording: a real WebM (%r)" % media[:8])
        out = os.environ.get("COMMS_SHOTS")
        if out:
            with open(os.path.join(out, "selftest-recording.webm"), "wb") as fh:
                fh.write(media)
        w = result["webrtc"]
        self.assertEqual((w["state"], w["dtlsState"]), ("connected", "connected"), w)
        self.assertTrue(w["srtpCipher"], "media is SRTP-encrypted: %s" % w)
        print("\n  headless Chromium %s: WebCrypto key %s; text+attachment round trip; plaintext refused; Lumi decrypts the browser's message;"
              " recording %s %d bytes (%d chunks) opens with Lumi's key only; WebRTC %s / %s / %s"
              % (subprocess.run([chromium, "--version"], capture_output=True, text=True).stdout.strip(), result["key_kid"][:8], result["recording_mime"],
                 len(media), len(rec["chunks"]), w["dtlsState"], w["srtpCipher"], w.get("dtlsCipher")))

    # ============================================================ 8. nothing readable on disk
    def test_99_no_plaintext_on_disk(self):
        self.assertTrue(len(PLAINTEXTS) >= 5, PLAINTEXTS)
        needles = []
        for s in PLAINTEXTS:
            needles += [s.encode(), base64.b64encode(s.encode())[:24], s.encode("utf-16-le")]
        hits, n = [], 0
        for dp, ds, fs in os.walk(self.cfg["data_dir"]):
            for f in fs:
                n += 1
                with open(os.path.join(dp, f), "rb") as fh:
                    raw = fh.read()
                hits += ["%s in %s" % (x[:20], f) for x in needles if x in raw]
        self.assertEqual(hits, [], "the team box never stores plaintext (or plain base64 of it)")
        print("\n  scanned %d files under the data dir for %d plaintexts (raw, base64, utf-16): none found" % (n, len(PLAINTEXTS)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
