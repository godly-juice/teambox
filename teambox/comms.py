"""MESSAGES, CALLS and VIDEO CALLS - the server side (the owner, 2026-10-21: "a facetime thing, a messaging place, a calls place, and
these need to be encrypted with only my Lumi having the decryption key").

WHAT THE TEAM BOX STORES AND WHAT IT CAN NOT DO
- Every message is encrypted IN THE SENDER'S BROWSER (WebCrypto): a fresh random AES-256-GCM content key per message, and that key is
  wrapped (RSA-OAEP-3072, SHA-256) for every device key of every member of the thread AND for Lumi's public key. The team box stores
  only the ciphertext, the wrapped keys and minimal metadata (thread, sender, time, sizes). It never has a private key, so it can not
  decrypt what it stores. Lumi's PRIVATE key exists only on BESNIK (lumi_comms_key.py); the box gets the public key in its config.
- Attachments: the same content key, one AES-GCM IV per uploaded chunk.
- Call recordings: the CALLER's browser records the call, encrypts each chunk with a content key wrapped ONLY for Lumi, and uploads
  the chunks during the call. Nobody on the team box - not even the members of the call - can decrypt a recording; only Lumi.
- Call media itself goes browser to browser (WebRTC, always DTLS-SRTP). The box only relays signalling (SDP / ICE) - that signalling
  IS visible to the box (it contains IP addresses and the DTLS fingerprints; the UI shows a safety code to compare out loud).
- METADATA IS VISIBLE TO THE SERVER: who is in which thread, who sent when, message and file sizes, who called whom and how long.

What the server checks (it can not see inside, so it checks the shape): the envelope format, that Lumi's key is one of the recipients
(and the ONLY one for recordings), that every wrapped key is a 3072-bit RSA block for a key that is registered to a member of that
thread, and that the bytes do not look like plaintext (known file headers, mostly printable text, low entropy). Honest limit: a
deliberately disguised plaintext (e.g. already-compressed media without its header) can not be told apart from ciphertext by any
server; the check catches accidents and naive clients, not a determined cheater.

Layout under data_dir/comms/:
  keys.json                 {slug: [{kid, spki, label, kind: device|recovery, created, revoked}]}     (public keys only)
  acceptances.json          {slug: {version, label, at, email}}   + acceptances.jsonl (append-only history)
  threads/<tid>.json        {id, kind: direct|group, members[], created, created_by, last_at, count}
  messages/<tid>.jsonl      one envelope per line {id, seq, thread, from, at, env, att[]}
  blobs/<id>.json + .bin    encrypted attachment / recording chunks (meta holds per-chunk iv + size)
  calls/<cid>.json          {id, thread, members, started_by, video, started, ended, joined, recording}
  events.jsonl              {seq, type: msg|rec, ...} - what the BESNIK export reads
  seq.json                  the global event counter
"""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time

from . import store as S

ALG = "RSA-OAEP-3072-SHA256+AES-256-GCM"
LOCK = threading.RLock()
RSA_OID = bytes.fromhex("2a864886f70d010101")          # 1.2.840.113549.1.1.1 rsaEncryption
KID_RX = re.compile(r"^[0-9a-f]{32}$")
ID_RX = re.compile(r"^[0-9a-z-]{6,48}$")

DEFAULTS = {
    "lumi_public_key": "",          # base64 SPKI DER of Lumi's RSA-OAEP-3072 public key (printed by lumi_comms_key.py on BESNIK)
    "lumi_kid": "",                 # optional double check: must equal sha256(SPKI)[:32 hex] when set
    "export_token_sha256": "",      # sha256 (hex) of the export token BESNIK's review script uses; empty = export switched off
    "ice_servers": [],              # e.g. [{"urls": ["stun:turn.xseed.ing:3478"]}]  (empty = same-LAN calls only)
    "turn": {"urls": [], "secret_file": "", "ttl_s": 3600},   # coturn "use-auth-secret": time-limited credentials per member
    "max_group": 20,                # members per thread
    "max_call": 4,                  # people per call (browser-to-browser mesh; more needs an SFU - not built)
    "keys_per_member": 5,
    "chunk_max_kb": 512,
    "attachment_max_mb": 25,
    "recording_max_mb": 400,
    "message_max_kb": 96,
    "notice_label": "2026-10-21",
}

# known plaintext file headers (attachments and recordings must never start with one of these)
MAGIC = [b"\x1a\x45\xdf\xa3", b"OggS", b"RIFF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"%PDF", b"PK\x03\x04", b"ID3", b"fLaC",
         b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x20ftyp", b"{\"", b"[", b"<"]


class CommsError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def cfg_of(app_cfg):
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (app_cfg.get("comms") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------------------------------------------- byte helpers
def b64d(s, what, lo=0, hi=None):
    if not isinstance(s, str) or len(s) > 4 * 1024 * 1024:
        raise CommsError(400, "%s must be base64 text" % what)
    try:
        b = base64.b64decode(s.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        raise CommsError(400, "%s is not valid base64" % what)
    if len(b) < lo or (hi is not None and len(b) > hi):
        raise CommsError(400, "%s has the wrong size (%d bytes)" % (what, len(b)))
    return b


def b64e(b):
    return base64.b64encode(b).decode("ascii")


def kid_of(spki_der):
    return hashlib.sha256(spki_der).hexdigest()[:32]


def _der(b, i):
    """-> (tag, value bytes, next index) for one DER element at b[i]."""
    tag = b[i]
    n = b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4:
            raise ValueError("bad length")
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    if i + n > len(b):
        raise ValueError("truncated")
    return tag, b[i:i + n], i + n


def rsa_spki_info(spki):
    """Parse a SubjectPublicKeyInfo (what WebCrypto exportKey('spki') gives) -> (modulus bits, exponent). Raises ValueError."""
    t, seq, end = _der(spki, 0)
    if t != 0x30 or end != len(spki):
        raise ValueError("not a SEQUENCE")
    t, alg, j = _der(seq, 0)
    if t != 0x30:
        raise ValueError("no algorithm")
    t, oid, _ = _der(alg, 0)
    if t != 0x06 or oid != RSA_OID:
        raise ValueError("not an RSA key")
    t, bits, _ = _der(seq, j)
    if t != 0x03 or not bits or bits[0] != 0:
        raise ValueError("no key bits")
    t, inner, _ = _der(bits[1:], 0)
    t1, n, k = _der(inner, 0)
    t2, e, _ = _der(inner, k)
    if t1 != 0x02 or t2 != 0x02:
        raise ValueError("bad RSA numbers")
    return int.from_bytes(n, "big").bit_length(), int.from_bytes(e, "big")


def check_rsa3072(spki):
    try:
        bits, e = rsa_spki_info(spki)
    except (ValueError, IndexError):
        raise CommsError(400, "the public key is not an RSA SubjectPublicKeyInfo")
    if bits != 3072 or e != 65537:
        raise CommsError(400, "the public key must be RSA 3072-bit with exponent 65537 (got %d-bit, e=%d)" % (bits, e))


def looks_plain(b):
    """True when bytes look like plaintext instead of AES-GCM output. Catches accidents/naive clients - see the module docstring."""
    if not b:
        return True
    for m in MAGIC:
        if b.startswith(m):
            return True
    if len(b) >= 8 and b[4:8] == b"ftyp":
        return True
    sample = b[:4096]
    printable = sum(1 for x in sample if 32 <= x < 127 or x in (9, 10, 13))
    if printable / len(sample) > 0.7:
        return True
    if len(sample) >= 256:
        cnt = [0] * 256
        for x in sample:
            cnt[x] += 1
        n = len(sample)
        ent = -sum(c / n * math.log2(c / n) for c in cnt if c)
        expect = 8 - 255 / (2 * n * math.log(2))           # what uniformly random bytes give at this sample size
        if ent < expect - 0.5:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------- the store
class Comms:
    def __init__(self, app):
        self.app = app
        self.st = app.store
        self.dir = app.store.p("comms")
        for d in ("threads", "messages", "blobs", "calls"):
            os.makedirs(os.path.join(self.dir, d), exist_ok=True)
        self.queues = {}            # slug -> [(seq, at, signal)]  (call signalling: in memory only, never written to disk)
        self.qseq = 0
        self.qcond = threading.Condition()

    @property
    def c(self):
        return cfg_of(self.app.cfg)

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    # ------------------------------------------------------------ Lumi's public key (from config)
    def lumi(self):
        c = self.c
        s = (c.get("lumi_public_key") or "").strip()
        if not s:
            return {"configured": False, "why": "Lumi's public key is not set in the team box config (comms.lumi_public_key) - "
                                                "messages and calls stay switched off until it is"}
        try:
            der = base64.b64decode(s, validate=True)
            check_rsa3072(der)
        except (ValueError, CommsError) as exc:
            return {"configured": False, "why": "comms.lumi_public_key in the config is not a valid RSA-3072 public key (%s)" % exc}
        kid = kid_of(der)
        if c.get("lumi_kid") and c["lumi_kid"] != kid:
            return {"configured": False, "why": "comms.lumi_kid does not match comms.lumi_public_key - check the config for a typo"}
        return {"configured": True, "spki": s, "kid": kid, "alg": ALG}

    def require_lumi(self):
        lu = self.lumi()
        if not lu["configured"]:
            raise CommsError(503, lu["why"])
        return lu

    # ------------------------------------------------------------ the monitoring notice gate
    def acceptance(self, slug):
        return (S.jread(self.p("acceptances.json"), {}) or {}).get(slug)

    def accepted(self, slug, version):
        a = self.acceptance(slug)
        return bool(a and a.get("version") == version)

    def accept(self, slug, email, version, label):
        rec = {"member": slug, "email": email, "version": version, "label": label, "at": S.now_s(), "ts": time.time()}
        with LOCK:
            allacc = S.jread(self.p("acceptances.json"), {}) or {}
            allacc[slug] = rec
            S.jwrite(self.p("acceptances.json"), allacc)
            with open(self.p("acceptances.jsonl"), "a", encoding="utf8") as fh:
                fh.write(json.dumps(rec) + "\n")
        return rec

    def acceptances(self):
        return S.jread(self.p("acceptances.json"), {}) or {}

    # ------------------------------------------------------------ member public keys
    def all_keys(self):
        return S.jread(self.p("keys.json"), {}) or {}

    def keys_of(self, slug, active=True):
        return [k for k in self.all_keys().get(slug, []) if not (active and k.get("revoked"))]

    def add_key(self, slug, spki_b64, label, kind):
        der = b64d(spki_b64, "public key", 200, 1000)
        check_rsa3072(der)
        kind = kind if kind in ("device", "recovery") else "device"
        kid = kid_of(der)
        if kid == self.lumi().get("kid"):
            raise CommsError(400, "that is Lumi's key, not yours")
        with LOCK:
            allk = self.all_keys()
            mine = allk.setdefault(slug, [])
            for k in mine:
                if k["kid"] == kid:
                    if k.get("revoked"):
                        raise CommsError(409, "this key was revoked - make a new one")
                    return k
            for other, lst in allk.items():
                if other != slug and any(k["kid"] == kid for k in lst):
                    raise CommsError(409, "this key belongs to another member")
            if len([k for k in mine if not k.get("revoked")]) >= int(self.c["keys_per_member"]):
                raise CommsError(409, "you already have %d keys - revoke an old browser's key first" % self.c["keys_per_member"])
            k = {"kid": kid, "spki": b64e(der), "label": str(label or kind)[:60], "kind": kind, "created": S.now_s(), "revoked": None}
            mine.append(k)
            S.jwrite(self.p("keys.json"), allk)
            return k

    def revoke_key(self, slug, kid):
        with LOCK:
            allk = self.all_keys()
            for k in allk.get(slug, []):
                if k["kid"] == kid and not k.get("revoked"):
                    k["revoked"] = S.now_s()
                    S.jwrite(self.p("keys.json"), allk)
                    return True
        return False

    # ------------------------------------------------------------ threads
    def thread(self, tid):
        if not ID_RX.match(tid or ""):
            return None
        return S.jread(self.p("threads", tid + ".json"))

    def thread_for(self, slug, tid):
        t = self.thread(tid)
        if not t or slug not in t["members"]:
            raise CommsError(404, "no such conversation")       # same answer for "not yours" and "does not exist"
        return t

    def threads_of(self, slug):
        out = []
        for f in os.listdir(self.p("threads")):
            t = S.jread(self.p("threads", f))
            if t and slug in t["members"]:
                out.append(t)
        out.sort(key=lambda t: t.get("last_at") or t["created"], reverse=True)
        return out

    def all_threads(self):
        return [t for t in (S.jread(self.p("threads", f)) for f in sorted(os.listdir(self.p("threads")))) if t]

    def create_thread(self, slug, members):
        known = {v["slug"] for v in self.st.members().values()}
        ms = sorted(set([slug] + [str(m) for m in (members or []) if isinstance(m, str)]))
        for m in ms:
            if m not in known:
                raise CommsError(400, "no such member: %s" % m)
        if len(ms) < 2:
            raise CommsError(400, "pick at least one other person")
        if len(ms) > int(self.c["max_group"]):
            raise CommsError(400, "at most %d people in one conversation" % self.c["max_group"])
        kind = "direct" if len(ms) == 2 else "group"
        with LOCK:
            if kind == "direct":
                for t in self.threads_of(slug):
                    if t["kind"] == "direct" and sorted(t["members"]) == ms:
                        return t
            tid = time.strftime("%Y%m%d-") + secrets.token_hex(5)
            t = {"id": tid, "kind": kind, "members": ms, "created": S.now_s(), "created_by": slug, "last_at": None, "count": 0}
            S.jwrite(self.p("threads", tid + ".json"), t)
            return t

    # ------------------------------------------------------------ envelopes
    def check_envelope(self, env, thread, lumi_only=False, need_iv=True, need_ct=True):
        """Validate an encrypted envelope against the thread's members' registered keys + Lumi's key. -> cleaned envelope."""
        lu = self.require_lumi()
        if not isinstance(env, dict) or env.get("v") != 1 or env.get("alg") != ALG:
            raise CommsError(400, "the message must be an encrypted envelope (v 1, %s)" % ALG)
        out = {"v": 1, "alg": ALG}
        if need_iv:
            out["iv"] = b64e(b64d(env.get("iv"), "iv", 12, 12))
        if need_ct:
            ct = b64d(env.get("ct"), "ciphertext", 17, int(self.c["message_max_kb"]) * 1024)
            if looks_plain(ct):
                raise CommsError(422, "refused: this does not look encrypted - the team box only stores ciphertext")
            out["ct"] = b64e(ct)
        keys = env.get("keys")
        if not isinstance(keys, list) or not keys or len(keys) > 200:
            raise CommsError(400, "the envelope needs its wrapped keys")
        members = thread["members"] if thread else []
        reg = {m: {k["kid"] for k in self.keys_of(m)} for m in members}
        seen, clean = set(), []
        for k in keys:
            if not isinstance(k, dict):
                raise CommsError(400, "bad wrapped key")
            to, kid = str(k.get("to") or ""), str(k.get("kid") or "")
            wk = b64d(k.get("wk"), "wrapped key", 384, 384)
            if to == "lumi":
                if kid != lu["kid"]:
                    raise CommsError(422, "the wrapped key for Lumi is for a different key than the one in the team box config")
            else:
                if lumi_only:
                    raise CommsError(422, "refused: a call recording may be wrapped ONLY for Lumi's key")
                if to not in reg:
                    raise CommsError(422, "refused: %s is not in this conversation" % to)
                if kid not in reg[to]:
                    raise CommsError(422, "refused: key %s is not a current key of %s - reload and try again" % (kid[:8], to))
            if (to, kid) in seen:
                continue
            seen.add((to, kid))
            clean.append({"to": to, "kid": kid, "wk": b64e(wk)})
        if ("lumi", lu["kid"]) not in seen:
            raise CommsError(422, "refused: every message and recording must also be encrypted for Lumi's key")
        if not lumi_only:
            for m, kids in reg.items():
                if kids and not any((m, kd) in seen for kd in kids):
                    raise CommsError(422, "refused: not encrypted for %s's current key(s) - reload and try again" % m)
        out["keys"] = clean
        return out

    def _next_seq(self):
        with LOCK:
            s = (S.jread(self.p("seq.json"), {}) or {}).get("seq", 0) + 1
            S.jwrite(self.p("seq.json"), {"seq": s})
            return s

    def _event(self, ev):
        with LOCK:
            ev["seq"] = self._next_seq()
            with open(self.p("events.jsonl"), "a", encoding="utf8") as fh:
                fh.write(json.dumps(ev) + "\n")
            return ev["seq"]

    def send(self, slug, tid, env, att):
        t = self.thread_for(slug, tid)
        clean = self.check_envelope(env, t)
        att_ids = []
        for bid in (att or [])[:10]:
            b = self.blob_meta(str(bid))
            if not b or b["owner"] != slug or b["kind"] != "att" or b.get("thread") != tid or not b.get("done") or b.get("used"):
                raise CommsError(400, "attachment %s is not a finished upload of yours for this conversation" % bid)
            att_ids.append(b["id"])
        with LOCK:
            mid = time.strftime("%Y%m%d%H%M%S-") + secrets.token_hex(4)
            rec = {"id": mid, "thread": tid, "from": slug, "at": S.now_s(), "ts": time.time(), "env": clean, "att": att_ids}
            rec["seq"] = self._event({"type": "msg", "thread": tid, "id": mid})
            with open(self.p("messages", tid + ".jsonl"), "a", encoding="utf8") as fh:
                fh.write(json.dumps(rec) + "\n")
            for bid in att_ids:
                b = self.blob_meta(bid)
                b["used"] = mid
                S.jwrite(self.p("blobs", bid + ".json"), b)
            t = self.thread(tid)
            t["last_at"], t["count"] = rec["at"], int(t.get("count") or 0) + 1
            S.jwrite(self.p("threads", tid + ".json"), t)
        return rec

    def messages(self, slug, tid, after=0):
        self.thread_for(slug, tid)
        return self._messages(tid, after)

    def _messages(self, tid, after=0):
        out = []
        p = self.p("messages", tid + ".jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf8") as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get("seq", 0) > after:
                        out.append(r)
        return out

    # ------------------------------------------------------------ blobs (attachments + recordings), uploaded in encrypted chunks
    def blob_meta(self, bid):
        if not ID_RX.match(bid or ""):
            return None
        return S.jread(self.p("blobs", bid + ".json"))

    def blob_start(self, slug, kind, thread=None, call=None, env=None, size=None):
        c = self.c
        if kind == "att":
            t = self.thread_for(slug, str(thread or ""))
            meta = {"kind": "att", "thread": t["id"], "max": int(c["attachment_max_mb"]) * 1024 * 1024}
        elif kind == "rec":
            cl = self.call(str(call or ""))
            if not cl or slug not in cl["members"]:
                raise CommsError(404, "no such call")
            if cl["started_by"] != slug:
                raise CommsError(403, "only the caller's browser records the call for Lumi")
            if cl.get("ended"):
                raise CommsError(409, "this call has ended")
            meta = {"kind": "rec", "call": cl["id"], "thread": cl["thread"], "max": int(c["recording_max_mb"]) * 1024 * 1024,
                    "env": self.check_envelope(env, None, lumi_only=True, need_iv=False, need_ct=False)}
        else:
            raise CommsError(400, "kind must be att or rec")
        bid = time.strftime("%Y%m%d-") + secrets.token_hex(6)
        meta.update({"id": bid, "owner": slug, "created": S.now_s(), "chunks": [], "bytes": 0, "done": False})
        with LOCK:
            S.jwrite(self.p("blobs", bid + ".json"), meta)
            open(self.p("blobs", bid + ".bin"), "wb").close()
            if kind == "rec":
                cl = self.call(meta["call"])
                cl["recording"] = {"blob": bid, "chunks": 0, "bytes": 0, "started": S.now_s(), "last_at": None, "done": False}
                self._save_call(cl)
        if kind == "rec":
            self._notify_call(self.call(meta["call"]), {"type": "state"})
        return meta

    def blob_chunk(self, slug, bid, i, iv_b64, data_b64):
        b = self.blob_meta(bid)
        if not b or b["owner"] != slug:
            raise CommsError(404, "no such upload")
        if b["done"]:
            raise CommsError(409, "this upload is finished")
        iv = b64d(iv_b64, "iv", 12, 12)
        data = b64d(data_b64, "chunk", 17, int(self.c["chunk_max_kb"]) * 1024 + 16)
        if looks_plain(data):
            raise CommsError(422, "refused: this chunk does not look encrypted - the team box only stores ciphertext")
        with LOCK:
            b = self.blob_meta(bid)
            if i != len(b["chunks"]):
                raise CommsError(409, "expected chunk %d" % len(b["chunks"]))
            if any(x["iv"] == b64e(iv) for x in b["chunks"]):
                raise CommsError(422, "refused: an IV may never be reused with the same key")
            if b["bytes"] + len(data) > b["max"]:
                raise CommsError(413, "the upload is over its size limit (%d MB)" % (b["max"] // (1024 * 1024)))
            with open(self.p("blobs", bid + ".bin"), "ab") as fh:
                off = fh.tell()
                fh.write(data)
            b["chunks"].append({"i": i, "iv": b64e(iv), "off": off, "len": len(data)})
            b["bytes"] += len(data)
            S.jwrite(self.p("blobs", bid + ".json"), b)
            if b["kind"] == "rec":
                cl = self.call(b["call"])
                if cl and cl.get("recording"):
                    cl["recording"].update(chunks=len(b["chunks"]), bytes=b["bytes"], last_at=S.now_s())
                    self._save_call(cl)
        if b["kind"] == "rec" and (i == 0 or i % 3 == 0):
            self._notify_call(self.call(b["call"]), {"type": "state"})
        return {"id": bid, "chunks": len(b["chunks"]), "bytes": b["bytes"]}

    def blob_finish(self, slug, bid):
        with LOCK:
            b = self.blob_meta(bid)
            if not b or b["owner"] != slug:
                raise CommsError(404, "no such upload")
            if not b["chunks"]:
                raise CommsError(400, "nothing was uploaded")
            if not b["done"]:
                b["done"] = True
                b["finished"] = S.now_s()
                S.jwrite(self.p("blobs", bid + ".json"), b)
                if b["kind"] == "rec":
                    self._event({"type": "rec", "blob": bid, "call": b["call"]})
                    cl = self.call(b["call"])
                    if cl and cl.get("recording"):
                        cl["recording"].update(done=True, chunks=len(b["chunks"]), bytes=b["bytes"])
                        self._save_call(cl)
        if b["kind"] == "rec":
            self._notify_call(self.call(b["call"]), {"type": "state"})
        return {k: b[k] for k in ("id", "kind", "bytes", "done")}

    def blob_chunk_get(self, bid, i):
        b = self.blob_meta(bid)
        if not b or not (0 <= i < len(b["chunks"])):
            raise CommsError(404, "no such chunk")
        ch = b["chunks"][i]
        with open(self.p("blobs", bid + ".bin"), "rb") as fh:
            fh.seek(ch["off"])
            data = fh.read(ch["len"])
        return {"id": bid, "i": i, "n": len(b["chunks"]), "iv": ch["iv"], "data": b64e(data)}

    def blob_get_for(self, slug, bid, i):
        b = self.blob_meta(bid)
        if not b or b["kind"] != "att" or not b.get("used"):
            raise CommsError(404, "no such attachment")
        self.thread_for(slug, b["thread"])
        return self.blob_chunk_get(bid, i)

    # ------------------------------------------------------------ calls: signalling only (media is browser to browser)
    def call(self, cid):
        if not ID_RX.match(cid or ""):
            return None
        return S.jread(self.p("calls", cid + ".json"))

    def _save_call(self, cl):
        S.jwrite(self.p("calls", cl["id"] + ".json"), cl)

    def call_start(self, slug, tid, video):
        t = self.thread_for(slug, tid)
        self.require_lumi()
        if len(t["members"]) > int(self.c["max_call"]):
            raise CommsError(409, "calls work for up to %d people for now (browser-to-browser). Bigger group calls need a media "
                                  "server (SFU) - not built yet" % self.c["max_call"])
        for cl in self.active_calls(slug):
            if cl["thread"] == tid:
                return cl
        cid = time.strftime("%Y%m%d-") + secrets.token_hex(5)
        cl = {"id": cid, "thread": tid, "members": t["members"], "started_by": slug, "video": bool(video), "started": S.now_s(),
              "ts": time.time(), "ended": None, "joined": [slug], "recording": None}
        with LOCK:
            self._save_call(cl)
        self._notify_call(cl, {"type": "ring", "from": slug})
        return cl

    def active_calls(self, slug=None):
        out = []
        for f in os.listdir(self.p("calls")):
            cl = S.jread(self.p("calls", f))
            if cl and not cl.get("ended") and (slug is None or slug in cl["members"]):
                if time.time() - cl.get("ts", 0) > 12 * 3600:
                    continue
                out.append(cl)
        return out

    def all_calls(self):
        return [c for c in (S.jread(self.p("calls", f)) for f in sorted(os.listdir(self.p("calls")))) if c]

    def call_for(self, slug, cid):
        cl = self.call(cid)
        if not cl or slug not in cl["members"]:
            raise CommsError(404, "no such call")
        return cl

    def call_join(self, slug, cid):
        with LOCK:
            cl = self.call_for(slug, cid)
            if cl.get("ended"):
                raise CommsError(409, "this call has ended")
            before = [m for m in cl["joined"] if m != slug]
            if slug not in cl["joined"]:
                cl["joined"].append(slug)
                self._save_call(cl)
        self._notify_call(cl, {"type": "joined", "who": slug})
        return cl, before

    def call_leave(self, slug, cid, end=False):
        with LOCK:
            cl = self.call_for(slug, cid)
            if slug in cl["joined"]:
                cl["joined"].remove(slug)
            if end or not cl["joined"]:
                cl["ended"] = cl.get("ended") or S.now_s()
                cl["seconds"] = int(time.time() - cl.get("ts", time.time()))
            self._save_call(cl)
            rec = cl.get("recording")
            if cl.get("ended") and rec and not rec.get("done"):
                b = self.blob_meta(rec["blob"])           # the caller's tab closed mid-call: keep (and export) what arrived
                if b and b["chunks"] and not b["done"]:
                    self.blob_finish(b["owner"], b["id"])
                cl = self.call(cid)
        self._notify_call(cl, {"type": "left" if not cl.get("ended") else "ended", "who": slug})
        return cl

    def signal(self, slug, cid, to, typ, data):
        cl = self.call_for(slug, cid)
        if cl.get("ended"):
            raise CommsError(409, "this call has ended")
        if to not in cl["members"] or to == slug:
            raise CommsError(400, "they are not in this call")
        if typ not in ("offer", "answer", "ice", "bye"):
            raise CommsError(400, "unknown signal")
        if len(json.dumps(data)) > 64 * 1024:
            raise CommsError(413, "signal too big")
        self._push(to, {"call": cid, "from": slug, "type": typ, "data": data})

    def call_public(self, cl):
        return {k: cl.get(k) for k in ("id", "thread", "members", "started_by", "video", "started", "ended", "joined", "recording", "seconds")}

    def _notify_call(self, cl, sig):
        if not cl:
            return
        for m in cl["members"]:
            self._push(m, dict(sig, call=cl["id"], state=self.call_public(cl)))

    def _push(self, slug, sig):
        with self.qcond:
            self.qseq += 1
            q = self.queues.setdefault(slug, [])
            q.append((self.qseq, time.time(), sig))
            cut = time.time() - 120
            while q and q[0][1] < cut:
                q.pop(0)
            self.qcond.notify_all()

    def poll(self, slug, since, wait=0.0):
        end = time.time() + max(0.0, min(float(wait), 20.0))
        with self.qcond:
            while True:
                q = self.queues.get(slug, [])
                if since > self.qseq:                       # the server restarted: the browser's counter is from before
                    since = 0
                got = [dict(s, seq=n) for n, _, s in q if n > since]
                left = end - time.time()
                if got or left <= 0:
                    return got, self.qseq
                self.qcond.wait(left)

    # ------------------------------------------------------------ TURN credentials (coturn use-auth-secret)
    def ice(self, slug):
        c = self.c
        out = list(c.get("ice_servers") or [])
        t = c.get("turn") or {}
        if t.get("urls") and t.get("secret_file"):
            try:
                with open(t["secret_file"], encoding="utf8") as fh:
                    sec = fh.read().strip()
            except OSError:
                sec = ""
            if sec:
                user = "%d:%s" % (int(time.time()) + int(t.get("ttl_s") or 3600), slug)
                cred = base64.b64encode(hmac.new(sec.encode(), user.encode(), hashlib.sha1).digest()).decode()
                out.append({"urls": t["urls"], "username": user, "credential": cred})
        return out

    # ------------------------------------------------------------ export for BESNIK (Lumi's review pipeline)
    def export_since(self, cursor, limit=500):
        evs = []
        p = self.p("events.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf8") as fh:
                for ln in fh:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if e["seq"] > cursor:
                        evs.append(e)
                        if len(evs) >= limit:
                            break
        lu = self.lumi()
        items, msg_cache = [], {}
        for e in evs:
            if e["type"] == "msg":
                if e["thread"] not in msg_cache:
                    msg_cache[e["thread"]] = {m["id"]: m for m in self._messages(e["thread"])}
                m = msg_cache[e["thread"]].get(e["id"])
                if not m:
                    continue
                env = dict(m["env"], keys=[k for k in m["env"]["keys"] if k["to"] == "lumi"])     # Lumi's wrapped key only
                atts = []
                for bid in m.get("att") or []:
                    b = self.blob_meta(bid) or {}
                    atts.append({"id": bid, "chunks": len(b.get("chunks") or []), "bytes": b.get("bytes")})
                items.append({"seq": e["seq"], "type": "msg", "id": m["id"], "thread": m["thread"], "from": m["from"], "at": m["at"],
                              "env": env, "att": atts})
            elif e["type"] == "rec":
                b = self.blob_meta(e["blob"]) or {}
                cl = self.call(e["call"]) or {}
                items.append({"seq": e["seq"], "type": "rec", "id": e["blob"], "call": e["call"], "thread": cl.get("thread"),
                              "by": b.get("owner"), "members": cl.get("members"), "started": cl.get("started"), "ended": cl.get("ended"),
                              "env": b.get("env"), "chunks": len(b.get("chunks") or []), "bytes": b.get("bytes")})
        threads = {t["id"]: {"members": t["members"], "kind": t["kind"]} for t in self.all_threads()}
        members = {v["slug"]: e for e, v in self.st.members().items()}
        nxt = evs[-1]["seq"] if evs else cursor
        return {"cursor": nxt, "more": len(evs) >= limit, "lumi_kid": lu.get("kid"), "items": items, "threads": threads, "members": members}
