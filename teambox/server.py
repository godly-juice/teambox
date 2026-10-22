"""THE TEAM BOX web app (team.xseed.ing). Standard library only: http.server + git + bubblewrap.
Binds to loopback only; the box's own cloudflared (Cloudflare Access email login in front) is the only way in.
Every /api request: identity from identity.resolve (Access email, trusted only from the local cloudflared), then a role check ON THE
SERVER (members: own things only; admins: everything; promote: admins only). POSTs need the header X-Teambox: 1 (a cross-site page
can not send it), so a logged-in browser can not be tricked into acting."""
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C
from . import githttp, identity, jail, modules, tour
from . import store as S
from .builder import Builds
from .gateway import Gateway
from .sandboxes import SandboxError, Sandboxes

STATIC = os.path.join(C.HERE, "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json", ".txt": "text/plain; charset=utf-8"}
SEC_HEADERS = [("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
               ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
               ("Cache-Control", "no-store")]
SETTING_KEYS = ("system_prompt", "temperature", "top_p", "max_tokens", "ctx")


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Req:
    def __init__(self, h, method, path, query, body, email, slug, admin):
        self.h, self.method, self.path, self.query, self.body = h, method, path, query, body
        self.email, self.slug, self.admin = email, slug, admin
        self.q = dict(urllib.parse.parse_qsl(query))


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = S.Store(cfg["data_dir"])
        self.sb = Sandboxes(cfg, self.store)
        self.gw = Gateway(cfg["gateway"])
        self.builds = Builds(cfg, self.store, self.sb, self.gw)
        self.logf = self.store.p("teambox.log")
        self.routes = []
        self._mount()

    def log(self, msg):
        with open(self.logf, "a", encoding="utf8") as fh:
            fh.write("%s %s\n" % (S.now_s(), msg))

    # ------------------------------------------------------------------ routing table
    def route(self, method, pattern, fn, admin=False):
        self.routes.append((method, re.compile("^" + pattern + "$"), fn, admin))

    def _mount(self):
        r = self.route
        r("GET", "/api/me", self.me)
        r("POST", "/api/profile", self.profile_save)
        r("GET", "/api/tour", self.tour_get)
        r("POST", "/api/tour", self.tour_save)
        r("GET", "/api/modules", lambda q: (200, {"modules": modules.registry()}))
        r("GET", "/api/sandboxes", self.sandboxes_list)
        r("POST", "/api/sandboxes", self.sandbox_create)
        r("GET", r"/api/sandboxes/([a-z0-9-]+)/([a-z0-9-]+)", self.sandbox_get)
        r("GET", r"/api/sandboxes/([a-z0-9-]+)/([a-z0-9-]+)/file", self.sandbox_file)
        r("POST", r"/api/sandboxes/([a-z0-9-]+)/([a-z0-9-]+)/test", self.sandbox_test)
        r("GET", "/api/builds", self.builds_list)
        r("POST", "/api/builds", self.build_start)
        r("GET", r"/api/builds/([0-9a-z-]+)", self.build_get)
        r("POST", r"/api/builds/([0-9a-z-]+)/stop", self.build_stop)
        r("POST", r"/api/builds/([0-9a-z-]+)/reject", self.build_reject)
        r("POST", r"/api/builds/([0-9a-z-]+)/apply", self.build_apply)
        r("POST", r"/api/builds/([0-9a-z-]+)/promote", self.build_promote, admin=True)
        r("GET", "/api/tripwire", self.trip_list, admin=True)
        r("POST", r"/api/tripwire/([0-9a-f]{64})/letpass", self.trip_letpass, admin=True)
        r("GET", "/api/models", self.models)
        r("POST", "/api/models/(start|stop)", self.model_action)
        r("GET", "/api/versions", self.versions_get)
        r("POST", "/api/versions", self.version_save)
        r("POST", "/api/versions/delete", self.version_delete)
        r("GET", "/api/chats", lambda q: (200, {"chats": self.store.chats(q.slug)}))
        r("POST", "/api/chats", self.chat_new)
        r("GET", r"/api/chats/([0-9a-z-]+)", self.chat_get)
        r("POST", r"/api/chats/([0-9a-z-]+)/send", self.chat_send)
        r("GET", "/api/tokens", self.tokens_list)
        r("POST", "/api/tokens", self.token_create)
        r("POST", r"/api/tokens/([0-9a-f]+)/revoke", self.token_revoke)
        r("GET", "/api/admin/members", self.admin_members, admin=True)
        r("GET", "/api/admin/team-main", lambda q: (200, self.sb.team_main_info()), admin=True)
        for (meth, path), fn in modules.routes(self).items():
            r(meth, re.escape(path), fn)

    def dispatch(self, req):
        for method, rx, fn, admin in self.routes:
            m = rx.match(req.path)
            if m and method == req.method:
                if admin and not req.admin:
                    raise HttpError(403, "admins only")
                return fn(req, *m.groups())
        raise HttpError(404, "no such endpoint")

    # ------------------------------------------------------------------ helpers
    def can_see(self, req, owner):
        return req.admin or owner == req.slug

    def sandbox_or_404(self, req, owner, name):
        if not self.can_see(req, owner) or not self.sb.exists(owner, name):
            raise HttpError(404, "no such sandbox")              # same answer for "not yours" and "does not exist"
        return self.sb.path(owner, name)

    def build_or_404(self, req, bid):
        try:
            r = self.builds.get(bid)
        except SandboxError:
            raise HttpError(404, "no such build")
        if not self.can_see(req, r["owner"]):
            raise HttpError(404, "no such build")
        return r

    # ------------------------------------------------------------------ me / profile / tour
    def me(self, q):
        pr = self.store.profile(q.slug) or {}
        return 200, {"email": q.email, "id": q.slug, "role": "admin" if q.admin else "team", "admin": q.admin,
                     "display_name": pr.get("display_name"), "language": pr.get("language"), "tour": pr.get("tour"),
                     "first_visit": bool(pr.get("_new")) or not (pr.get("tour") or {}).get("seen"), "public_url": self.cfg["public_url"],
                     "dev_mode": bool(self.cfg.get("dev_mode")), "jail": jail.available(self.cfg["jail"])[1] or "ok"}

    def profile_save(self, q):
        pr = self.store.profile(q.slug)
        dn = str(q.body.get("display_name") or "").strip()[:60]
        lang = str(q.body.get("language") or "").strip()[:12]
        if dn:
            pr["display_name"] = dn
        if lang and re.match(r"^[a-zA-Z-]{2,12}$", lang):
            pr["language"] = lang
        self.store.save_profile(q.slug, pr)
        return 200, {"ok": True}

    def tour_get(self, q):
        pr = self.store.profile(q.slug)
        return 200, dict(tour.tour_payload(self.cfg["public_url"], self.cfg.get("feeds")), progress=pr.get("tour"))

    def tour_save(self, q):
        pr = self.store.profile(q.slug)
        t = pr.setdefault("tour", {"seen": False, "done": []})
        ids = {c["id"] for c in tour.CHECKPOINTS}
        if q.body.get("seen"):
            t["seen"] = True
        d = q.body.get("done")
        if isinstance(d, str) and d in ids and d not in t["done"]:
            t["done"].append(d)
        if q.body.get("reset"):
            t["done"] = []
        self.store.save_profile(q.slug, pr)
        return 200, {"progress": t}

    # ------------------------------------------------------------------ sandboxes
    def sandboxes_list(self, q):
        if q.admin and q.q.get("all"):
            return 200, {"sandboxes": self.sb.all()}
        return 200, {"sandboxes": self.sb.list(q.slug)}

    def sandbox_create(self, q):
        try:
            return 200, {"sandbox": self.sb.create(q.slug, q.body.get("name"), q.body.get("label"))}
        except SandboxError as exc:
            raise HttpError(400, str(exc))

    def sandbox_get(self, q, owner, name):
        self.sandbox_or_404(q, owner, name)
        info = self.sb.info(owner, name)
        info["clone_url"] = "%s/git/%s/%s.git" % (self.cfg["public_url"].rstrip("/"), owner, name)
        info["builds"] = [b for b in self.builds.list(owner) if b["sandbox"] == name][:30]
        return 200, info

    def sandbox_file(self, q, owner, name):
        self.sandbox_or_404(q, owner, name)
        try:
            return 200, {"path": q.q.get("path"), "text": self.sb.read_file(owner, name, q.q.get("path"))}
        except SandboxError as exc:
            raise HttpError(404, str(exc))

    def sandbox_test(self, q, owner, name):
        p = self.sandbox_or_404(q, owner, name)
        if owner != q.slug:
            raise HttpError(403, "only the owner runs tests in a sandbox")
        lk = self.sb.lock(owner, name)
        if not lk.acquire(blocking=False):
            raise HttpError(409, "this sandbox is busy")
        try:
            return 200, self.builds.jail_test(p, self.sb.test_command(p), "manual")
        finally:
            lk.release()

    # ------------------------------------------------------------------ builds
    def builds_list(self, q):
        return 200, {"builds": self.builds.list(None if q.admin and q.q.get("all") else q.slug), "running": len(self.builds.running),
                     "max_total": self.cfg["builds"]["max_total"]}

    def build_start(self, q):
        b = q.body
        name = str(b.get("sandbox") or "")
        self.sandbox_or_404(q, q.slug, name)                      # builds only in your OWN sandbox (admins too)
        settings = None
        if b.get("version") and b.get("version") != "original":
            v = next((x for x in self.store.versions(q.slug).get(str(b.get("model")), []) if x["id"] == b.get("version")), None)
            if not v:
                raise HttpError(400, "no such settings version")
            settings = v["settings"]
        try:
            rec = self.builds.start(q.slug, q.email, name, b.get("task"), str(b.get("model") or ""), settings)
        except SandboxError as exc:
            raise HttpError(409, str(exc))
        self.log("build %s started by %s in %s" % (rec["id"], q.email, name))
        return 200, {"build": rec}

    def build_get(self, q, bid):
        self.build_or_404(q, bid)
        out = self.builds.review(bid)
        out["can_promote"] = q.admin and out["status"] == "waiting for review"
        out["can_reject"] = (q.admin or out["owner"] == q.slug) and out["status"] not in ("promoted", "rejected", "running", "testing")
        if q.admin:
            t = self.builds.trip()
            out["tripwire_pending"] = [p for p in t["pending"] if p.get("build") == bid]
        else:
            out["tripwire_pending"] = [{k: p[k] for k in ("file", "word", "line", "why", "text")} for p in self.builds.trip()["pending"]
                                       if p.get("build") == bid]
        return 200, out

    def build_stop(self, q, bid):
        r = self.build_or_404(q, bid)
        if r["owner"] != q.slug and not q.admin:
            raise HttpError(403, "not your build")
        self.builds.stop(bid)
        return 200, {"ok": True}

    def build_reject(self, q, bid):
        self.build_or_404(q, bid)
        try:
            r = self.builds.reject(bid, q.email, q.admin, q.slug)
        except PermissionError as exc:
            raise HttpError(403, str(exc))
        except SandboxError as exc:
            raise HttpError(409, str(exc))
        self.log("build %s rejected by %s" % (bid, q.email))
        return 200, {"build": r}

    def build_apply(self, q, bid):
        self.build_or_404(q, bid)
        try:
            return 200, {"build": self.builds.apply(bid, q.slug)}
        except PermissionError as exc:
            raise HttpError(403, str(exc))
        except SandboxError as exc:
            raise HttpError(409, str(exc))

    def build_promote(self, q, bid):
        if not q.admin:                                           # second, explicit check (the route is admin-only too)
            raise HttpError(403, "admins only")
        if q.body.get("confirm") is not True:
            raise HttpError(400, "confirm:true and the reviewed head commit are required")
        try:
            r = self.builds.promote(bid, q.email, str(q.body.get("head") or ""))
        except SandboxError as exc:
            raise HttpError(409, str(exc))
        self.log("build %s PROMOTED by %s -> team-main %s" % (bid, q.email, r.get("promoted_commit")))
        return 200, {"build": r}

    def trip_list(self, q):
        return 200, self.builds.trip()

    def trip_letpass(self, q, sha):
        hit = self.builds.letpass(sha, q.email)
        if not hit:
            raise HttpError(404, "no blocked test with that id")
        self.log("tripwire let-pass %s (%s line %s) by %s" % (sha[:12], hit["file"], hit["line"], q.email))
        return 200, {"ok": True, "approved": hit}

    # ------------------------------------------------------------------ models (through BESNIK's gateway)
    def models(self, q):
        status, j, why = self.gw.call("GET", "/team/models", q.email, timeout=20)
        if status != 200:
            return 200, {"ok": False, "refused": why, "status": status, "models": []}
        return 200, dict(j, ok=True)

    def model_action(self, q, action):
        model = str(q.body.get("model") or "")
        if not model:
            raise HttpError(400, "which model?")
        status, j, why = self.gw.call("POST", "/team/" + action, q.email, {"model": model}, timeout=60)
        self.log("model %s %s by %s -> %s %s" % (action, model, q.email, status, why[:200]))
        return 200, {"ok": 200 <= status < 300 and (j or {}).get("ok", True) is not False, "status": status, "answer": j, "refused": why}

    # ------------------------------------------------------------------ settings versions
    def clean_settings(self, s):
        if not isinstance(s, dict):
            raise HttpError(400, "settings must be an object")
        out = {}
        if s.get("system_prompt") not in (None, ""):
            out["system_prompt"] = str(s["system_prompt"])[:20000]
        for k, typ, lo, hi in (("temperature", float, 0, 5), ("top_p", float, 0, 1), ("max_tokens", int, 1, 200000), ("ctx", int, 256, 1000000)):
            v = s.get(k)
            if v in (None, ""):
                continue
            try:
                v = typ(v)
            except (TypeError, ValueError):
                raise HttpError(400, "%s must be a number" % k)
            if not lo <= v <= hi:
                raise HttpError(400, "%s must be between %s and %s" % (k, lo, hi))
            out[k] = v
        return out

    def versions_get(self, q):
        model = q.q.get("model") or ""
        lst = self.store.versions(q.slug).get(model, [])
        return 200, {"model": model, "versions": [{"id": "original", "name": "Original", "settings": {}, "original": True,
                                                    "note": "the model's own settings, as shared - always kept"}] + lst}

    def version_save(self, q):
        model = str(q.body.get("model") or "")
        if not model or len(model) > 80:
            raise HttpError(400, "which model?")
        try:
            v = self.store.save_version(q.slug, model, q.body.get("name"), self.clean_settings(q.body.get("settings") or {}))
        except ValueError as exc:
            raise HttpError(400, str(exc))
        return 200, {"version": v}

    def version_delete(self, q):
        try:
            ok = self.store.delete_version(q.slug, str(q.body.get("model") or ""), str(q.body.get("id") or ""))
        except ValueError as exc:
            raise HttpError(400, str(exc))
        return 200, {"ok": ok}

    def settings_for(self, slug, model, vid):
        if not vid or vid == "original":
            return {}
        v = next((x for x in self.store.versions(slug).get(model, []) if x["id"] == vid), None)
        if not v:
            raise HttpError(400, "no such settings version")
        return v["settings"]

    # ------------------------------------------------------------------ chats
    def chat_new(self, q):
        model = str(q.body.get("model") or "")
        vid = str(q.body.get("version") or "original")
        if not model:
            raise HttpError(400, "which model?")
        self.settings_for(q.slug, model, vid)
        return 200, {"chat": self.store.new_chat(q.slug, model, vid)}

    def chat_get(self, q, cid):
        c = self.store.chat(q.slug, cid)                           # a member's own chats only - path is their own folder
        if not c:
            raise HttpError(404, "no such chat")
        return 200, {"chat": c}

    def chat_send(self, q, cid):
        c = self.store.chat(q.slug, cid)
        if not c:
            raise HttpError(404, "no such chat")
        text = str(q.body.get("text") or "").strip()
        if not text:
            raise HttpError(400, "say something")
        if len(c["messages"]) >= int(self.cfg["limits"]["chat_messages_max"]):
            raise HttpError(409, "this chat is full - start a new one")
        st = self.settings_for(q.slug, c["model"], c.get("version"))
        c["messages"].append({"role": "user", "content": text[:20000], "at": S.now_s()})
        msgs = [{"role": m["role"], "content": m["content"]} for m in c["messages"] if m["role"] in ("user", "assistant")]
        if st.get("ctx"):                                          # the member's ctx setting: keep the history inside ~ctx tokens
            budget = int(st["ctx"]) * 3
            while len(msgs) > 1 and sum(len(m["content"]) for m in msgs) > budget:
                msgs.pop(0)
        if st.get("system_prompt"):
            msgs.insert(0, {"role": "system", "content": st["system_prompt"]})
        body = {"model": c["model"], "messages": msgs}
        for k in ("temperature", "top_p", "max_tokens"):
            if st.get(k) is not None:
                body[k] = st[k]
        status, j, why = self.gw.call("POST", "/v1/chat/completions", q.email, body)
        if status == 200 and j and j.get("choices"):
            ans = (j["choices"][0].get("message") or {}).get("content") or ""
            c["messages"].append({"role": "assistant", "content": ans, "at": S.now_s()})
            if c["title"] == "New chat":
                c["title"] = text[:60]
            self.store.save_chat(q.slug, c)
            return 200, {"chat": c, "answer": ans}
        c["messages"].append({"role": "notice", "content": "Not sent to the model: %s" % why, "at": S.now_s()})
        self.store.save_chat(q.slug, c)
        return 200, {"chat": c, "refused": why, "status": status}

    # ------------------------------------------------------------------ git tokens
    def tokens_list(self, q):
        return 200, {"tokens": [{"id": k, "label": v["label"], "created": v["created"], "last_used": v["last_used"]}
                                for k, v in self.store.tokens(q.slug).items()],
                     "git_base": "%s/git/%s/" % (self.cfg["public_url"].rstrip("/"), q.slug)}

    def token_create(self, q):
        try:
            tid, tok = self.store.create_token(q.slug, str(q.body.get("label") or "my AI tool"), int(self.cfg["limits"]["tokens_per_member"]))
        except ValueError as exc:
            raise HttpError(400, str(exc))
        self.log("token %s created by %s" % (tid, q.email))
        return 200, {"id": tid, "token": tok, "note": "copy it now - it is shown only once"}

    def token_revoke(self, q, tid):
        if not self.store.revoke_token(q.slug, tid):
            raise HttpError(404, "no such token")
        self.log("token %s revoked by %s" % (tid, q.email))
        return 200, {"ok": True}

    # ------------------------------------------------------------------ admin
    def admin_members(self, q):
        out = []
        for email, v in self.store.members().items():
            pr = self.store.profile(v["slug"]) or {}
            out.append({"email": email, "id": v["slug"], "display_name": pr.get("display_name"), "admin": email in self.cfg["admin_emails"],
                        "sandboxes": [s["name"] for s in self.sb.list(v["slug"])], "created": v.get("created")})
        return 200, {"members": out}


# ====================================================================== HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "teambox"
    sys_version = ""
    protocol_version = "HTTP/1.0"                 # one request per connection: no keep-alive state to get wrong
    app = None

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in list(SEC_HEADERS) + list(extra):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self.handle_any("GET")

    def do_HEAD(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")

    def handle_any(self, method):
        app = self.app
        path, _, query = self.path.partition("?")
        path = urllib.parse.unquote(path)
        try:
            if path.startswith("/git/"):
                return githttp.handle(app, self, method, path, query)
            if path == "/healthz":
                return self.send(200, {"ok": True})
            if path in ("/how-it-works", "/how-it-works.html"):        # public: the verification prompt points here
                return self.static("how-it-works.html")
            if path == "/api/public/tour":
                return self.send(200, tour.tour_payload(app.cfg["public_url"], app.cfg.get("feeds")))
            if path == "/api/public/notice":
                from .modules import messages as _msg
                return self.send(200, {"notice": _msg.NOTICE, "sha256": _msg.NOTICE_SHA256})
            if path.startswith("/static/"):
                return self.static(path[len("/static/"):])
            if path == "/dev/login" and app.cfg.get("dev_mode"):        # DEV ONLY (local testing/screenshots): pick an identity
                e = urllib.parse.parse_qs(query).get("email", [""])[0].strip().lower()
                if not S.EMAIL_RX.match(e):
                    return self.send(400, {"error": "email=?"})
                nxt = urllib.parse.parse_qs(query).get("next", ["/"])[0]
                nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
                self.send_response(302)
                self.send_header("Set-Cookie", "dev_email=%s; Path=/; SameSite=Strict; HttpOnly" % urllib.parse.quote(e))
                self.send_header("Location", nxt)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            email, why = identity.resolve(app.cfg, self.client_address[0], self.headers)
            if not email:
                if path.startswith("/api/"):
                    return self.send(401, {"error": why})
                import html as _h
                return self.send(401, ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                                       "<title>Team box</title><link rel=stylesheet href='/static/app.css'><body><main class=page>"
                                       "<h1>Team box</h1><p>%s</p><p><a href='/how-it-works'>How this site works</a></p></main>" % _h.escape(why)).encode(),
                                 "text/html; charset=utf-8")
            slug = app.store.slug_for(email)
            pr = app.store.ensure_profile(slug, email)
            app.sb.ensure_default(slug)
            admin = identity.is_admin(app.cfg, email)
            raw = modules.raw_route(app, method, path)            # module uploads/downloads: body not read here (streamed)
            if raw:
                if method == "POST" and self.headers.get("X-Teambox") != "1":
                    return self.send(403, {"error": "missing X-Teambox header (cross-site requests are refused)"})
                return raw(self, Req(self, method, path, query, None, email, slug, admin))
            if not path.startswith("/api/"):
                if method != "GET":
                    return self.send(405, {"error": "GET only"})
                return self.static("index.html")
            body = {}
            if method == "POST":
                if self.headers.get("X-Teambox") != "1":
                    return self.send(403, {"error": "missing X-Teambox header (cross-site requests are refused)"})
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1024 * 1024:
                    return self.send(413, {"error": "request too big"})
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self.send(400, {"error": "the body is not JSON"})
                if not isinstance(body, dict):
                    return self.send(400, {"error": "the body must be a JSON object"})
            req = Req(self, method, path, query, body, email, slug, admin)
            if pr.get("_new"):
                req_new = True
            else:
                req_new = False
            code, obj = app.dispatch(req)
            if req_new and path == "/api/me":
                obj["first_visit"] = True
            return self.send(code, obj)
        except HttpError as exc:
            return self.send(exc.code, {"error": exc.msg})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:  # noqa: BLE001
            app.log("ERROR %s %s: %r\n%s" % (method, path, exc, traceback.format_exc()[-2000:]))
            try:
                return self.send(500, {"error": "the team box had an error (logged)"})
            except OSError:
                return None

    def static(self, rel):
        rel = rel.replace("\\", "/")
        if ".." in rel.split("/") or rel.startswith("/"):
            return self.send(404, {"error": "not found"})
        p = os.path.join(STATIC, rel)
        if not os.path.isfile(p):
            return self.send(404, {"error": "not found"})
        with open(p, "rb") as fh:
            data = fh.read()
        return self.send(200, data, MIME.get(os.path.splitext(p)[1], "application/octet-stream"))


def make_server(cfg):
    app = App(cfg)
    H = type("H", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((cfg["bind"], int(cfg["port"])), H)
    srv.daemon_threads = True
    return app, srv


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    cfg = C.load(a[a.index("--config") + 1] if "--config" in a else None)
    if "--port" in a:
        cfg["port"] = int(a[a.index("--port") + 1])
    app, srv = make_server(cfg)
    modules.start(app)                                             # e.g. the Tuesday 14:00 team-feed scheduler
    ok, why = jail.available(cfg["jail"])
    app.log("team box listening on %s:%s (data %s, dev_mode %s, jail %s)" % (cfg["bind"], cfg["port"], cfg["data_dir"], cfg["dev_mode"], why or "ok"))
    print("team box on http://%s:%s  (jail: %s)" % (cfg["bind"], cfg["port"], why or "ok"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
