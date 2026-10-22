"""The HOME FEED (Marjus, 2026-10-21: "in the home page, add a feed where right now its just comments ppl can post but also
they should be able to attach files to that post, videos, pictures, notepad, spreadbooks, etc.").

Every signed-in member sees the home feed. A post = text + up to N attachments, plus comments and emoji reactions.
The author can edit and delete their own post; an admin can delete any post (but not edit it).
Uploads (see uploads.py for the type/serving rules):
  POST /api/m/feed/upload?name=<file name>   body = the raw file bytes (Content-Length required)  -> a PENDING file of yours
  POST /api/m/feed/posts {text, attachments: [file ids]}                                        -> the post
Size caps from config "feeds": max_file_mb per file, member_quota_mb for everything one member has uploaded (pending + posted).
Layout under data_dir:
  feed/posts/<id>.json       {id, author, author_email, text, created, edited, attachments: [file ids], comments, reactions}
  feed/files/<32 hex>        the bytes, random name, no extension, outside the web root (static/)
  feed/files.json            {file id: {owner, name, ext, kind, size, created, post}}
  feed/previews/<id>.json    text / table preview (strings only), made once at upload time"""
import os
import re
import secrets
import threading
import time

from .. import store as S
from . import social, uploads

MODULE = {
    "id": "feed",
    "title": "Home feed",
    "nav": "Home feed",
    "status": "live",
    "summary": "Posts from the team with attachments: videos, pictures, notes, spreadsheets, PDFs and documents.",
    "help": "Anyone on the team can post here and attach files (videos, pictures, notes, spreadsheets, PDFs, documents). Everyone on the "
            "team sees the home feed. You can edit or delete your own posts; admins can delete any post.",
}

DEFAULTS = {"max_file_mb": 95, "member_quota_mb": 1000, "max_files_per_post": 10, "post_max_chars": 10000, "comment_max_chars": 4000,
            "pending_hours": 24}
LOCK = threading.RLock()
ID_RX = re.compile(r"^[0-9a-z-]{8,60}$")
FID_RX = re.compile(r"^[0-9a-f]{32}$")


def fail(code, msg):
    from ..server import HttpError
    raise HttpError(code, msg)


def conf(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("feeds") or {}).items() if k in DEFAULTS})
    return out


class Feed:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.dir = app.store.p("feed")
        for d in ("posts", "files", "previews"):
            os.makedirs(os.path.join(self.dir, d), exist_ok=True)
        self.index_f = os.path.join(self.dir, "files.json")

    @property
    def c(self):
        return conf(self.app.cfg)

    # ------------------------------------------------------------ helpers
    def name(self, slug):
        pr = self.store.profile(slug) if S.SLUG_RX.match(slug or "") else None
        return (pr or {}).get("display_name") or slug

    def files(self):
        return S.jread(self.index_f, {}) or {}

    def fpath(self, fid):
        if not FID_RX.match(fid or ""):
            fail(404, "no such file")
        return os.path.join(self.dir, "files", fid)

    def used(self, slug):
        return sum(m["size"] for m in self.files().values() if m["owner"] == slug)

    def post_path(self, pid):
        if not ID_RX.match(pid or ""):
            fail(404, "no such post")
        return os.path.join(self.dir, "posts", pid + ".json")

    def get_post(self, pid):
        p = S.jread(self.post_path(pid))
        if not p:
            fail(404, "no such post")
        return p

    def save_post(self, p):
        S.jwrite(self.post_path(p["id"]), p)

    def purge_pending(self, slug):
        """Uploaded but never posted within pending_hours: removed (and the quota freed)."""
        cutoff = time.time() - float(self.c["pending_hours"]) * 3600
        with LOCK:
            idx = self.files()
            gone = [f for f, m in idx.items() if m["owner"] == slug and not m.get("post") and m.get("t", 0) < cutoff]
            for f in gone:
                self._remove_file(f)
                idx.pop(f, None)
            if gone:
                S.jwrite(self.index_f, idx)

    def _remove_file(self, fid):
        for p in (os.path.join(self.dir, "files", fid), os.path.join(self.dir, "previews", fid + ".json")):
            try:
                os.remove(p)
            except OSError:
                pass

    def att_view(self, fid, meta):
        kind, _ct, inline, _m = uploads.TYPES.get(meta["ext"], ("download-only", "", False, None))
        return {"id": fid, "name": meta["name"], "ext": meta["ext"], "kind": kind, "size": meta["size"], "inline": inline,
                "has_preview": bool(meta.get("preview")), "url": "/api/m/feed/file?id=" + fid,
                "download": "/api/m/feed/file?id=%s&download=1" % fid}

    def post_view(self, p, q, idx=None):
        idx = self.files() if idx is None else idx
        v = {"id": p["id"], "author": p["author"], "author_name": self.name(p["author"]), "text": p["text"], "created": p["created"],
             "edited": p.get("edited"), "attachments": [self.att_view(f, idx[f]) for f in p.get("attachments", []) if f in idx],
             "can_edit": p["author"] == q.slug, "can_delete": p["author"] == q.slug or q.admin}
        v.update(social.public_view(p, q.slug, self.name))
        return v

    def take_files(self, q, ids, post_id, already=()):
        ids = [str(x) for x in (ids or [])]
        if len(set(ids) | set(already)) > int(self.c["max_files_per_post"]):
            fail(400, "at most %d attachments per post" % int(self.c["max_files_per_post"]))
        idx = self.files()
        for f in ids:
            m = idx.get(f)
            if not m or m["owner"] != q.slug or (m.get("post") and m["post"] != post_id):
                fail(400, "attachment %s is not one of your uploaded files" % f[:12])
        for f in ids:
            idx[f]["post"] = post_id
        return idx, ids

    # ------------------------------------------------------------ routes (JSON)
    def list_posts(self, q):
        names = sorted(os.listdir(os.path.join(self.dir, "posts")), reverse=True)
        before = q.q.get("before")
        if before:
            names = [n for n in names if n[:-5] < before]
        try:
            limit = max(1, min(int(q.q.get("limit") or 20), 50))
        except ValueError:
            limit = 20
        idx = self.files()
        out = []
        for n in names:
            if len(out) >= limit:
                break
            p = S.jread(os.path.join(self.dir, "posts", n))
            if p:
                out.append(self.post_view(p, q, idx))
        c = self.c
        return 200, {"posts": out, "more": len(names) > len(out), "emoji": social.EMOJI,
                     "limits": {"max_file_mb": c["max_file_mb"], "member_quota_mb": c["member_quota_mb"],
                                "max_files_per_post": c["max_files_per_post"], "used_mb": round(self.used(q.slug) / 2 ** 20, 2),
                                "types": sorted(uploads.TYPES)}}

    def create_post(self, q):
        text = str(q.body.get("text") or "").strip()
        if len(text) > int(self.c["post_max_chars"]):
            fail(400, "a post can be at most %d characters" % int(self.c["post_max_chars"]))
        att = q.body.get("attachments") or []
        if not isinstance(att, list):
            fail(400, "attachments must be a list of file ids")
        if not text and not att:
            fail(400, "write something or attach a file")
        pid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        with LOCK:
            idx, ids = self.take_files(q, att, pid)
            p = {"id": pid, "author": q.slug, "author_email": q.email, "text": text, "created": S.now_s(), "edited": None,
                 "attachments": ids, "comments": [], "reactions": {}}
            self.save_post(p)
            S.jwrite(self.index_f, idx)
        self.app.log("feed post %s by %s (%d files)" % (pid, q.email, len(ids)))
        return 200, {"post": self.post_view(p, q)}

    def edit_post(self, q):
        with LOCK:
            p = self.get_post(str(q.body.get("id") or ""))
            if p["author"] != q.slug:
                fail(403, "only the author can edit a post")
            text = str(q.body.get("text") if q.body.get("text") is not None else p["text"]).strip()
            if len(text) > int(self.c["post_max_chars"]):
                fail(400, "a post can be at most %d characters" % int(self.c["post_max_chars"]))
            remove = [str(x) for x in (q.body.get("remove") or [])]
            keep = [f for f in p["attachments"] if f not in remove]
            idx, added = self.take_files(q, q.body.get("add") or [], p["id"], keep)
            new_att = keep + [f for f in added if f not in keep]
            if not text and not new_att:
                fail(400, "a post needs text or an attachment - delete it instead")
            for f in remove:
                if f in p["attachments"] and f in idx:
                    self._remove_file(f)
                    idx.pop(f, None)
            p["attachments"] = new_att
            p["text"], p["edited"] = text, S.now_s()
            self.save_post(p)
            S.jwrite(self.index_f, idx)
        return 200, {"post": self.post_view(p, q)}

    def delete_post(self, q):
        with LOCK:
            p = self.get_post(str(q.body.get("id") or ""))
            if p["author"] != q.slug and not q.admin:
                fail(403, "only the author or an admin can delete a post")
            idx = self.files()
            for f in p.get("attachments", []):
                self._remove_file(f)
                idx.pop(f, None)
            S.jwrite(self.index_f, idx)
            os.remove(self.post_path(p["id"]))
        self.app.log("feed post %s deleted by %s" % (p["id"], q.email))
        return 200, {"ok": True}

    def _social(self, q, fn):
        with LOCK:
            p = self.get_post(str(q.body.get("post") or ""))
            try:
                res = fn(p)
            except social.SocialError as exc:
                fail(exc.code, exc.msg)
            self.save_post(p)
        return 200, dict(social.public_view(p, q.slug, self.name), result=res)

    def comment(self, q):
        return self._social(q, lambda p: social.add_comment(p, q.slug, q.email, q.body.get("text"), int(self.c["comment_max_chars"]))["id"])

    def comment_delete(self, q):
        return self._social(q, lambda p: social.delete_comment(p, str(q.body.get("comment") or ""), q.slug, q.admin))

    def react(self, q):
        return self._social(q, lambda p: social.toggle_reaction(p, q.slug, str(q.body.get("emoji") or "")))

    def visible_file(self, q, fid):
        meta = self.files().get(fid) if FID_RX.match(fid or "") else None
        if not meta or (not meta.get("post") and meta["owner"] != q.slug and not q.admin):
            fail(404, "no such file")                       # a pending upload is only its uploader's (and admins')
        return meta

    def preview(self, q):
        fid = q.q.get("id") or ""
        meta = self.visible_file(q, fid)
        pv = S.jread(os.path.join(self.dir, "previews", fid + ".json")) if meta.get("preview") else None
        return 200, {"id": fid, "name": meta["name"], "preview": pv or {"type": "none", "why": "no preview for this kind of file"}}

    def usage(self, q):
        return 200, {"used_bytes": self.used(q.slug), "quota_bytes": int(float(self.c["member_quota_mb"]) * 2 ** 20),
                     "max_file_bytes": int(float(self.c["max_file_mb"]) * 2 ** 20),
                     "pending": [self.att_view(f, m) for f, m in self.files().items() if m["owner"] == q.slug and not m.get("post")]}

    def discard(self, q):
        """Throw away one of your uploads that is not in a post (yet)."""
        fid = str(q.body.get("id") or "")
        with LOCK:
            idx = self.files()
            m = idx.get(fid)
            if not m or m["owner"] != q.slug or m.get("post"):
                fail(404, "no such pending upload of yours")
            self._remove_file(fid)
            idx.pop(fid, None)
            S.jwrite(self.index_f, idx)
        return 200, {"ok": True}

    # ------------------------------------------------------------ raw routes (streamed)
    def upload(self, h, q):
        if h.headers.get("Transfer-Encoding"):
            fail(411, "send the file with a Content-Length (no chunked uploads)")
        try:
            n = int(h.headers.get("Content-Length") or "-1")
        except ValueError:
            n = -1
        if n < 0:
            fail(411, "Content-Length is required")
        if n == 0:
            fail(400, "the file is empty")
        name = uploads.clean_name(q.q.get("name"))
        try:
            e, (kind, _ct, _inl, magic) = uploads.classify(name)
        except uploads.UploadError as exc:
            fail(exc.code, exc.msg)
        mx = int(float(self.c["max_file_mb"]) * 2 ** 20)
        quota = int(float(self.c["member_quota_mb"]) * 2 ** 20)
        if n > mx:
            fail(413, "that file is %.1f MB - the most per file is %s MB" % (n / 2 ** 20, self.c["max_file_mb"]))
        self.purge_pending(q.slug)
        used = self.used(q.slug)
        if used + n > quota:
            fail(413, "that would put you over your upload quota (%.1f of %s MB used) - delete some posts first"
                 % (used / 2 ** 20, self.c["member_quota_mb"]))
        fid = secrets.token_hex(16)
        dest = os.path.join(self.dir, "files", fid)
        tmp = dest + ".part"
        got, head = 0, b""
        try:
            with open(tmp, "wb") as fh:
                while got < n:
                    chunk = h.rfile.read(min(256 * 1024, n - got))
                    if not chunk:
                        break
                    if len(head) < 8192:
                        head += chunk[:8192 - len(head)]
                    fh.write(chunk)
                    got += len(chunk)
            if got != n:
                fail(400, "the upload was cut off (%d of %d bytes)" % (got, n))
            try:
                uploads.check_magic(e, magic, head)
            except uploads.UploadError as exc:
                fail(exc.code, exc.msg)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        pv = uploads.make_preview(dest, e)
        if pv:
            S.jwrite(os.path.join(self.dir, "previews", fid + ".json"), pv)
        with LOCK:
            idx = self.files()
            if sum(m["size"] for m in idx.values() if m["owner"] == q.slug) + n > quota:   # a parallel upload got there first
                self._remove_file(fid)
                fail(413, "that would put you over your upload quota")
            idx[fid] = {"owner": q.slug, "name": name, "ext": e, "kind": kind, "size": n, "created": S.now_s(), "t": time.time(),
                        "post": None, "preview": bool(pv)}
            S.jwrite(self.index_f, idx)
        return h.send(200, {"file": self.att_view(fid, idx[fid])})

    def download(self, h, q):
        fid = q.q.get("id") or ""
        meta = self.visible_file(q, fid)
        p = self.fpath(fid)
        if not os.path.isfile(p):
            fail(404, "no such file")
        from ..server import SEC_HEADERS
        return uploads.serve_file(h, p, meta, q.q.get("download") == "1", SEC_HEADERS)


def init(app):
    app.ext["feed"] = Feed(app)


def routes(app):
    f = app.ext["feed"]
    return {("GET", "/posts"): f.list_posts, ("POST", "/posts"): f.create_post, ("POST", "/post/edit"): f.edit_post,
            ("POST", "/post/delete"): f.delete_post, ("POST", "/comment"): f.comment, ("POST", "/comment/delete"): f.comment_delete,
            ("POST", "/react"): f.react, ("GET", "/preview"): f.preview, ("GET", "/usage"): f.usage, ("POST", "/upload/discard"): f.discard}


def raw_routes(app):
    f = app.ext["feed"]
    return {("POST", "/upload"): f.upload, ("GET", "/file"): f.download}
