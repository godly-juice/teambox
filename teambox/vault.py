"""THE VAULT - each member's personal encrypted storage (notes + files). Marjus, 2026-10-21: "a password for their vault" ... his
pick: Lumi CAN read it (locked with their password AND Lumi's key).

Everything secret happens in the member's BROWSER (static/vault.js, WebCrypto), like messages:
  - VAULT KEY (VK): a random AES-256-GCM key made in the browser. Stored on the box only WRAPPED: AES-GCM under a key derived from the
    member's VAULT PASSWORD with PBKDF2-SHA256 (600,000+ iterations, random 16-byte salt). So it opens on any device once they type the
    password; the server never sees the password, the vault key or any plaintext.
  - each ITEM (a note, or a file + its name/type) is encrypted with its own fresh AES-256-GCM content key (CK). CK is wrapped twice:
    for the member's vault key (AES-GCM) AND for Lumi's public key (RSA-OAEP-3072, the same key as messages - comms.lumi_public_key).
  - files are uploaded in encrypted 256 KB chunks (a fresh IV each, under CK).
  - changing the vault password re-wraps the vault key under the new password (the member's PIN is asked too, so a stolen session can
    not swap the password); items do not change.
The server checks shapes only: exactly one vault-wrapped and one Lumi-wrapped key (Lumi's current kid), sizes, unique IVs, and that the
bytes do not look like plaintext (same check as messages). A forgotten vault password can NOT be recovered by the member (Lumi can still
read the items). The monitoring notice covers the vault; the tour and how-it-works say so.
Layout: data_dir/vault/<member>/vault.json  items/<id>.json  blobs/<id>.<n>.bin      data_dir/vault/events.jsonl (for BESNIK's export)"""
import json
import os
import re
import secrets
import threading
import time

from . import comms as CM
from . import store as S

LOCK = threading.RLock()
VAULT_ALG = "AES-256-GCM(content) + AES-256-GCM(vault key) + RSA-OAEP-3072-SHA256(Lumi)"
KDF_NAME = "PBKDF2-SHA256"
KDF_MIN_ITER = 600000
ID_RX = re.compile(r"^v[0-9a-f]{16}$")
DEFAULTS = {"file_max_mb": 25, "member_max_mb": 500, "items_max": 2000, "note_max_kb": 256, "chunk_max_kb": 512}


class VaultError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def conf(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("vault") or {}).items() if k in DEFAULTS})
    return out


def b64d(s, what, lo=0, hi=None):
    try:
        return CM.b64d(s, what, lo, hi)
    except CM.CommsError as exc:
        raise VaultError(exc.code, exc.msg)


class Vault:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.dir = app.store.p("vault")
        os.makedirs(self.dir, exist_ok=True)

    @property
    def c(self):
        return conf(self.app.cfg)

    def lumi(self):
        cm = getattr(self.app, "comms", None)
        return cm.lumi() if cm else {"configured": False, "why": "messages are not switched on"}

    def p(self, slug, *parts):
        if not S.SLUG_RX.match(slug or ""):
            raise VaultError(400, "bad member id")
        return os.path.join(self.dir, slug, *parts)

    def record(self, slug):
        return S.jread(self.p(slug, "vault.json"))

    def exists(self, slug):
        return bool(self.record(slug))

    # ------------------------------------------------------------ the vault key's password wrap
    def _check_wrap(self, kdf, pw):
        if not isinstance(kdf, dict) or kdf.get("name") != KDF_NAME:
            raise VaultError(400, "kdf must be %s" % KDF_NAME)
        try:
            it = int(kdf.get("iter"))
        except (TypeError, ValueError):
            raise VaultError(400, "kdf.iter must be a number")
        if not KDF_MIN_ITER <= it <= 10000000:
            raise VaultError(400, "the password key must use at least %d PBKDF2 iterations" % KDF_MIN_ITER)
        salt = b64d(kdf.get("salt"), "kdf.salt", 16, 64)
        if not isinstance(pw, dict):
            raise VaultError(400, "pw must be the wrapped vault key")
        iv = b64d(pw.get("iv"), "pw.iv", 12, 12)
        wk = b64d(pw.get("wk"), "pw.wk", 48, 48)
        if CM.looks_plain(wk):
            raise VaultError(422, "refused: the wrapped vault key does not look encrypted")
        return {"name": KDF_NAME, "iter": it, "salt": CM.b64e(salt)}, {"iv": CM.b64e(iv), "wk": CM.b64e(wk)}

    def init(self, slug, kdf, pw):
        k, w = self._check_wrap(kdf, pw)
        with LOCK:
            if self.exists(slug):
                raise VaultError(409, "your vault already exists - use 'change vault password' instead")
            S.jwrite(self.p(slug, "vault.json"), {"v": 1, "alg": VAULT_ALG, "kdf": k, "pw": w, "created": S.now_s(), "changed": None})
        return self.record(slug)

    def change_password(self, slug, kdf, pw):
        k, w = self._check_wrap(kdf, pw)
        with LOCK:
            rec = self.record(slug)
            if not rec:
                raise VaultError(404, "you have no vault yet")
            if rec["pw"]["iv"] == w["iv"] or rec["kdf"]["salt"] == k["salt"]:
                raise VaultError(400, "a new password wrap needs a new salt and IV")
            rec.update(kdf=k, pw=w, changed=S.now_s())
            S.jwrite(self.p(slug, "vault.json"), rec)
        return rec

    # ------------------------------------------------------------ items
    def check_env(self, env, max_bytes):
        lu = self.lumi()
        if not lu.get("configured"):
            raise VaultError(503, lu.get("why") or "Lumi's key is not configured")
        if not isinstance(env, dict) or env.get("v") != 1 or env.get("alg") != VAULT_ALG:
            raise VaultError(400, "an item must be an encrypted envelope (v 1, %s)" % VAULT_ALG)
        iv = b64d(env.get("iv"), "iv", 12, 12)
        ct = b64d(env.get("ct"), "ciphertext", 17, max_bytes)
        if CM.looks_plain(ct):
            raise VaultError(422, "refused: this does not look encrypted - the vault stores ciphertext only")
        keys = env.get("keys")
        if not isinstance(keys, list) or len(keys) != 2:
            raise VaultError(422, "refused: an item's key must be wrapped exactly twice - for your vault key and for Lumi")
        out = {"v": 1, "alg": VAULT_ALG, "iv": CM.b64e(iv), "ct": CM.b64e(ct), "keys": []}
        got = set()
        for k in keys:
            if not isinstance(k, dict):
                raise VaultError(400, "bad wrapped key")
            to = k.get("to")
            if to == "vault":
                kiv = b64d(k.get("iv"), "vault key iv", 12, 12)
                wk = b64d(k.get("wk"), "vault-wrapped key", 48, 48)
                out["keys"].append({"to": "vault", "iv": CM.b64e(kiv), "wk": CM.b64e(wk)})
            elif to == "lumi":
                if k.get("kid") != lu["kid"]:
                    raise VaultError(422, "the wrapped key for Lumi is for a different key than the one in the team box config")
                wk = b64d(k.get("wk"), "Lumi-wrapped key", 384, 384)
                out["keys"].append({"to": "lumi", "kid": lu["kid"], "wk": CM.b64e(wk)})
            else:
                raise VaultError(422, "refused: vault items are wrapped only for your vault key and Lumi")
            got.add(to)
        if got != {"vault", "lumi"}:
            raise VaultError(422, "refused: every vault item must be encrypted for your vault key AND for Lumi's key")
        return out

    def items(self, slug):
        d = self.p(slug, "items")
        out = []
        if os.path.isdir(d):
            for f in sorted(os.listdir(d), reverse=True):
                it = S.jread(os.path.join(d, f))
                if it:
                    out.append(self.meta(it))
        return out

    @staticmethod
    def meta(it):
        return {k: it.get(k) for k in ("id", "kind", "created", "updated", "size", "status")} | {"chunks": len(it.get("chunks") or [])}

    def item(self, slug, iid):
        if not ID_RX.match(iid or ""):
            raise VaultError(404, "no such vault item")
        it = S.jread(self.p(slug, "items", iid + ".json"))
        if not it:
            raise VaultError(404, "no such vault item")
        return it

    def used(self, slug):
        tot = 0
        for m in self.items(slug):
            tot += int(m.get("size") or 0)
        return tot

    def _event(self, slug, iid, what):
        with LOCK:
            seqf = os.path.join(self.dir, "seq.json")
            seq = int((S.jread(seqf, {}) or {}).get("seq", 0)) + 1
            S.jwrite(seqf, {"seq": seq})
            with open(os.path.join(self.dir, "events.jsonl"), "a", encoding="utf8") as fh:
                fh.write(json.dumps({"seq": seq, "member": slug, "id": iid, "what": what, "at": S.now_s()}) + "\n")

    def add(self, slug, kind, env, size):
        if not self.exists(slug):
            raise VaultError(409, "set up your vault password first")
        if kind not in ("note", "file"):
            raise VaultError(400, "kind must be note or file")
        c = self.c
        try:
            size = int(size or 0)
        except (TypeError, ValueError):
            raise VaultError(400, "size must be a number")
        if kind == "file" and not 0 < size <= float(c["file_max_mb"]) * 2 ** 20:
            raise VaultError(413, "a vault file can be at most %s MB" % c["file_max_mb"])
        clean = self.check_env(env, int(c["note_max_kb"]) * 1024)
        with LOCK:
            items = self.items(slug)
            if len(items) >= int(c["items_max"]):
                raise VaultError(409, "your vault holds %d items already (the most)" % len(items))
            if self.used(slug) + size > float(c["member_max_mb"]) * 2 ** 20:
                raise VaultError(413, "that would put your vault over %s MB" % c["member_max_mb"])
            iid = "v" + secrets.token_hex(8)
            it = {"id": iid, "kind": kind, "env": clean, "created": S.now_s(), "updated": S.now_s(),
                  "size": size if kind == "file" else len(clean["ct"]) * 3 // 4, "status": "uploading" if kind == "file" else "ok",
                  "chunks": [], "declared": size}
            S.jwrite(self.p(slug, "items", iid + ".json"), it)
        self._event(slug, iid, "add")
        return self.meta(it)

    def update(self, slug, iid, env):
        with LOCK:
            it = self.item(slug, iid)
            if it["kind"] != "note":
                raise VaultError(400, "only notes can be edited - upload a new file instead")
            it["env"] = self.check_env(env, int(self.c["note_max_kb"]) * 1024)
            it["updated"], it["size"] = S.now_s(), len(it["env"]["ct"]) * 3 // 4
            S.jwrite(self.p(slug, "items", iid + ".json"), it)
        self._event(slug, iid, "update")
        return self.meta(it)

    def chunk(self, slug, iid, i, iv_b64, data_b64):
        c = self.c
        with LOCK:
            it = self.item(slug, iid)
            if it["kind"] != "file" or it.get("status") != "uploading":
                raise VaultError(409, "this item is not taking uploads")
            if i != len(it["chunks"]):
                raise VaultError(400, "chunks must arrive in order (expected %d)" % len(it["chunks"]))
            iv = b64d(iv_b64, "iv", 12, 12)
            data = b64d(data_b64, "chunk", 17, int(c["chunk_max_kb"]) * 1024 + 16)
            if CM.looks_plain(data):
                raise VaultError(422, "refused: this chunk does not look encrypted")
            if any(ch["iv"] == CM.b64e(iv) for ch in it["chunks"]) or it["env"]["iv"] == CM.b64e(iv):
                raise VaultError(422, "refused: an IV was used twice")
            got = sum(ch["n"] for ch in it["chunks"]) + len(data) - 16
            if got > it["declared"]:
                raise VaultError(413, "more data than the file's declared size")
            os.makedirs(self.p(slug, "blobs"), exist_ok=True)
            with open(self.p(slug, "blobs", "%s.%d.bin" % (iid, i)), "wb") as fh:
                fh.write(data)
            it["chunks"].append({"iv": CM.b64e(iv), "n": len(data) - 16})
            S.jwrite(self.p(slug, "items", iid + ".json"), it)
        return {"i": i, "got": got}

    def finish(self, slug, iid):
        with LOCK:
            it = self.item(slug, iid)
            if it["kind"] != "file":
                raise VaultError(400, "not a file")
            got = sum(ch["n"] for ch in it["chunks"])
            if got != it["declared"]:
                raise VaultError(400, "the upload is incomplete (%d of %d bytes)" % (got, it["declared"]))
            it["status"], it["updated"] = "ok", S.now_s()
            S.jwrite(self.p(slug, "items", iid + ".json"), it)
        self._event(slug, iid, "file")
        return self.meta(it)

    def chunk_get(self, slug, iid, i):
        it = self.item(slug, iid)
        if not 0 <= i < len(it["chunks"]):
            raise VaultError(404, "no such chunk")
        with open(self.p(slug, "blobs", "%s.%d.bin" % (iid, i)), "rb") as fh:
            data = fh.read()
        return {"i": i, "n": len(it["chunks"]), "iv": it["chunks"][i]["iv"], "data": CM.b64e(data)}

    def delete(self, slug, iid):
        with LOCK:
            it = self.item(slug, iid)
            for i in range(len(it.get("chunks") or [])):
                try:
                    os.remove(self.p(slug, "blobs", "%s.%d.bin" % (iid, i)))
                except OSError:
                    pass
            os.remove(self.p(slug, "items", iid + ".json"))
        self._event(slug, iid, "delete")

    # ------------------------------------------------------------ export for BESNIK (Lumi): ciphertext + Lumi's wrapped key only
    def export_since(self, cursor, limit=500):
        evs = []
        p = os.path.join(self.dir, "events.jsonl")
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
        items = []
        for e in evs:
            try:
                it = self.item(e["member"], e["id"])
            except VaultError:
                items.append({"seq": e["seq"], "type": "vault", "member": e["member"], "id": e["id"], "what": e["what"], "gone": True})
                continue
            env = dict(it["env"], keys=[k for k in it["env"]["keys"] if k["to"] == "lumi"])
            items.append({"seq": e["seq"], "type": "vault", "member": e["member"], "id": e["id"], "what": e["what"], "kind": it["kind"],
                          "env": env, "chunks": len(it.get("chunks") or []), "size": it.get("size"), "updated": it.get("updated")})
        return {"cursor": evs[-1]["seq"] if evs else cursor, "more": len(evs) >= limit, "items": items}


class Routes:
    def __init__(self, app, v):
        self.app, self.v = app, v

    def wrap(self, fn):
        def handler(q, *a):
            try:
                return fn(q, *a)
            except VaultError as exc:
                from .server import HttpError
                raise HttpError(exc.code, exc.msg)
        return handler

    def state(self, q):
        rec = self.v.record(q.slug)
        lu = self.v.lumi()
        c = self.v.c
        return 200, {"vault": rec, "lumi": {k: lu.get(k) for k in ("configured", "spki", "kid", "why")}, "items": self.v.items(q.slug) if rec else [],
                     "alg": VAULT_ALG, "kdf_min_iter": KDF_MIN_ITER, "used_bytes": self.v.used(q.slug) if rec else 0,
                     "limits": c, "monitoring": "Your vault is covered by the team's monitoring notice: Lumi (the owner's AI) can read it."}

    def init(self, q):
        return 200, {"vault": self.v.init(q.slug, q.body.get("kdf"), q.body.get("pw"))}

    def password(self, q):
        acc = getattr(q, "account", None)
        if acc:                                                    # a stolen session alone can not swap the password
            self.app.auth.check_pin_of(acc["id"], q.body.get("pin"), q.ip)
        return 200, {"vault": self.v.change_password(q.slug, q.body.get("kdf"), q.body.get("pw"))}

    def add(self, q):
        return 200, {"item": self.v.add(q.slug, str(q.body.get("kind") or ""), q.body.get("env"), q.body.get("size"))}

    def update(self, q):
        return 200, {"item": self.v.update(q.slug, str(q.body.get("id") or ""), q.body.get("env"))}

    def get(self, q):
        it = self.v.item(q.slug, q.q.get("id") or "")
        return 200, {"item": dict(self.v.meta(it), env=it["env"])}

    def chunk(self, q):
        try:
            i = int(q.body.get("i"))
        except (TypeError, ValueError):
            raise VaultError(400, "i must be a number")
        return 200, self.v.chunk(q.slug, str(q.body.get("id") or ""), i, q.body.get("iv"), q.body.get("data"))

    def finish(self, q):
        return 200, {"item": self.v.finish(q.slug, str(q.body.get("id") or ""))}

    def chunk_get(self, q):
        try:
            i = int(q.q.get("i") or 0)
        except ValueError:
            raise VaultError(400, "i must be a number")
        return 200, self.v.chunk_get(q.slug, q.q.get("id") or "", i)

    def delete(self, q):
        self.v.delete(q.slug, str(q.body.get("id") or ""))
        return 200, {"ok": True}


def mount(app, route):
    v = app.vault = Vault(app)
    r = Routes(app, v)
    w = r.wrap
    route("GET", "/api/vault", w(r.state))
    route("POST", "/api/vault/init", w(r.init))
    route("POST", "/api/vault/password", w(r.password))
    route("POST", "/api/vault/items", w(r.add))
    route("POST", "/api/vault/items/update", w(r.update))
    route("GET", "/api/vault/item", w(r.get))
    route("POST", "/api/vault/chunk", w(r.chunk))
    route("POST", "/api/vault/finish", w(r.finish))
    route("GET", "/api/vault/chunk", w(r.chunk_get))
    route("POST", "/api/vault/delete", w(r.delete))
