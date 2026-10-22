"""Small JSON-file store: members, profiles, tokens, per-member chats and settings versions.
Layout under data_dir:
  members.json                       {email: {slug, created}}
  members/<slug>/profile.json        display name, language, tour progress
  members/<slug>/chats/<id>.json     saved chats with the AIs
  members/<slug>/versions.json       {model: [ {id, name, settings, created} ]}   ("Original" is implicit, never stored, never deletable)
  members/<slug>/tokens.json         git tokens: {id: {label, hash, created, last_used}} (hash only - the token is shown once)
  tokens-index.json                  {sha256(token): {slug, id}}
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time

LOCK = threading.RLock()


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def jread(p, default=None):
    try:
        with open(p, encoding="utf8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def jwrite(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (p, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EMAIL_RX = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


class Store:
    def __init__(self, data_dir):
        self.dir = data_dir
        os.makedirs(os.path.join(data_dir, "members"), exist_ok=True)

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    # ---------------------------------------------------------------- members
    def members(self):
        return jread(self.p("members.json"), {}) or {}

    def slug_for(self, email, create=True):
        """The member's folder name: their email made path-safe (+ -2, -3 on a collision). Stable once created."""
        email = email.strip().lower()
        with LOCK:
            m = self.members()
            if email in m:
                return m[email]["slug"]
            if not create:
                return None
            base = re.sub(r"[^a-z0-9]+", "-", email).strip("-")[:56] or "member"
            used = {v["slug"] for v in m.values()}
            slug, n = base, 2
            while slug in used:
                slug, n = "%s-%d" % (base, n), n + 1
            m[email] = {"slug": slug, "created": now_s()}
            jwrite(self.p("members.json"), m)
            return slug

    def email_for(self, slug):
        for e, v in self.members().items():
            if v["slug"] == slug:
                return e
        return None

    def mdir(self, slug, *parts):
        if not SLUG_RX.match(slug or ""):
            raise ValueError("bad member id")
        return self.p("members", slug, *parts)

    # ---------------------------------------------------------------- profile
    def profile(self, slug):
        return jread(self.mdir(slug, "profile.json"), None)

    def ensure_profile(self, slug, email):
        with LOCK:
            pr = self.profile(slug)
            if pr is None:
                pr = {"email": email, "display_name": email.split("@")[0], "language": "en", "created": now_s(),
                      "tour": {"seen": False, "done": []}}
                jwrite(self.mdir(slug, "profile.json"), pr)
                pr["_new"] = True
            return pr

    def save_profile(self, slug, pr):
        pr = {k: v for k, v in pr.items() if not k.startswith("_")}
        with LOCK:
            jwrite(self.mdir(slug, "profile.json"), pr)

    # ---------------------------------------------------------------- git tokens (shown once, stored hashed)
    @staticmethod
    def hash_token(tok):
        return hashlib.sha256(tok.encode("utf8")).hexdigest()

    def tokens(self, slug):
        return jread(self.mdir(slug, "tokens.json"), {}) or {}

    def create_token(self, slug, label, max_n):
        with LOCK:
            toks = self.tokens(slug)
            if len(toks) >= max_n:
                raise ValueError("you already have %d tokens - revoke one first" % max_n)
            tok = "tbx_" + secrets.token_urlsafe(30)
            tid = secrets.token_hex(6)
            h = self.hash_token(tok)
            toks[tid] = {"label": (label or "token")[:60], "hash": h, "created": now_s(), "last_used": None}
            jwrite(self.mdir(slug, "tokens.json"), toks)
            idx = jread(self.p("tokens-index.json"), {}) or {}
            idx[h] = {"slug": slug, "id": tid}
            jwrite(self.p("tokens-index.json"), idx)
            return tid, tok

    def revoke_token(self, slug, tid):
        with LOCK:
            toks = self.tokens(slug)
            t = toks.pop(tid, None)
            if not t:
                return False
            jwrite(self.mdir(slug, "tokens.json"), toks)
            idx = jread(self.p("tokens-index.json"), {}) or {}
            idx.pop(t["hash"], None)
            jwrite(self.p("tokens-index.json"), idx)
            return True

    def check_token(self, tok):
        """-> member slug, or None. Constant-time on the hash; the token itself is never stored."""
        if not tok or not tok.startswith("tbx_") or len(tok) > 200:
            return None
        h = self.hash_token(tok)
        idx = jread(self.p("tokens-index.json"), {}) or {}
        hit = idx.get(h)
        if not hit:
            return None
        with LOCK:
            toks = self.tokens(hit["slug"])
            t = toks.get(hit["id"])
            if not t or not secrets.compare_digest(t["hash"], h):
                return None
            t["last_used"] = now_s()
            jwrite(self.mdir(hit["slug"], "tokens.json"), toks)
        return hit["slug"]

    # ---------------------------------------------------------------- settings versions
    def versions(self, slug):
        return jread(self.mdir(slug, "versions.json"), {}) or {}

    def save_version(self, slug, model, name, settings):
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("give the version a name")
        if name.lower() == "original":
            raise ValueError("'Original' is the model's own settings and is always kept - pick another name")
        with LOCK:
            v = self.versions(slug)
            lst = v.setdefault(model, [])
            if any(x["name"].lower() == name.lower() for x in lst):
                raise ValueError("you already have a version called '%s' for this model" % name)
            if len(lst) >= 50:
                raise ValueError("50 versions per model at most")
            ent = {"id": secrets.token_hex(5), "name": name, "settings": settings, "created": now_s()}
            lst.append(ent)
            jwrite(self.mdir(slug, "versions.json"), v)
            return ent

    def delete_version(self, slug, model, vid):
        if vid == "original":
            raise ValueError("the Original version can not be deleted")
        with LOCK:
            v = self.versions(slug)
            lst = v.get(model) or []
            keep = [x for x in lst if x["id"] != vid]
            if len(keep) == len(lst):
                return False
            v[model] = keep
            jwrite(self.mdir(slug, "versions.json"), v)
            return True

    # ---------------------------------------------------------------- chats
    def chats(self, slug):
        d = self.mdir(slug, "chats")
        out = []
        if os.path.isdir(d):
            for f in sorted(os.listdir(d), reverse=True):
                c = jread(os.path.join(d, f))
                if c:
                    out.append({k: c.get(k) for k in ("id", "title", "model", "version", "created", "updated")})
        return out

    def chat(self, slug, cid):
        if not re.match(r"^[0-9a-z-]{6,40}$", cid or ""):
            return None
        return jread(self.mdir(slug, "chats", cid + ".json"))

    def save_chat(self, slug, chat):
        chat["updated"] = now_s()
        with LOCK:
            jwrite(self.mdir(slug, "chats", chat["id"] + ".json"), chat)

    def new_chat(self, slug, model, version):
        cid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        c = {"id": cid, "title": "New chat", "model": model, "version": version, "created": now_s(), "messages": []}
        self.save_chat(slug, c)
        return c
