"""BRING-YOUR-OWN AI TOOL: each sandbox is a git remote over HTTPS at  <public_url>/git/<member>/<sandbox>.git
(git smart HTTP through `git http-backend`, run behind this app). Auth = HTTP Basic, password = a per-member token the member makes on
their profile page (shown once, stored hashed). A token reaches ONLY its owner's sandboxes (admins may also clone/fetch, never push).
Only the smart protocol's three requests are served. Hooks are off for every git process, the body size is capped, and a push that
changes main makes the server run the sandbox's tests in the jail and file the push as a reviewable build."""
import base64
import os
import re
import subprocess
import tempfile

from .sandboxes import GIT, SAFE, SandboxError, git

PATH_RX = re.compile(r"^/git/([a-z0-9][a-z0-9-]{0,62})/([a-z0-9][a-z0-9-]{0,39})\.git(/info/refs|/git-upload-pack|/git-receive-pack)$")


def token_from(headers):
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("basic "):
        try:
            _user, _, pw = base64.b64decode(auth[6:].strip()).decode("utf8", "replace").partition(":")
            return pw.strip()
        except ValueError:
            return ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def read_body(handler, limit):
    """The request body into a temp file (chunked or Content-Length), refusing anything over the limit. -> (file, size) or raises."""
    f = tempfile.TemporaryFile()
    total = 0
    rf = handler.rfile
    if (handler.headers.get("Transfer-Encoding") or "").lower() == "chunked":
        while True:
            line = rf.readline(1024).strip()
            n = int(line.split(b";")[0] or b"0", 16)
            if n == 0:
                while rf.readline(1024).strip():
                    pass
                break
            total += n
            if total > limit:
                raise ValueError("the push is bigger than the limit")
            left = n
            while left:
                b = rf.read(min(left, 65536))
                if not b:
                    raise ValueError("the connection ended mid-body")
                f.write(b)
                left -= len(b)
            rf.readline(8)
    else:
        n = int(handler.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("the push is bigger than the limit")
        left = n
        while left:
            b = rf.read(min(left, 65536))
            if not b:
                break
            f.write(b)
            left -= len(b)
            total += len(b)
    f.seek(0)
    return f, total


def deny(handler, code, text):
    data = (text + "\n").encode()
    handler.send_response(code)
    if code == 401:
        handler.send_header("WWW-Authenticate", 'Basic realm="team sandbox git - use your team box token as the password"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def handle(app, handler, method, path, query):
    m = PATH_RX.match(path)
    if not m:
        return deny(handler, 404, "not a team sandbox git address (expected /git/<member>/<sandbox>.git)")
    slug, name, rest = m.groups()
    service = None
    if rest == "/info/refs":
        if method != "GET":
            return deny(handler, 405, "GET only")
        q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        service = q.get("service")
        if service not in ("git-upload-pack", "git-receive-pack"):
            return deny(handler, 403, "only the smart git protocol is served (update your git)")
    else:
        if method != "POST":
            return deny(handler, 405, "POST only")
        service = rest[1:]
    tok = token_from(handler.headers)
    who = app.store.check_token(tok) if tok else None
    if not who:
        return deny(handler, 401, "a team box token is needed (make one on your Profile page; use it as the password)")
    who_email = app.store.email_for(who) or ""
    is_admin = who_email in app.cfg["admin_emails"]
    if who != slug and not (is_admin and service == "git-upload-pack"):
        return deny(handler, 403, "this token can only reach its owner's own sandboxes")
    try:
        repo = app.sb.path(slug, name)
    except SandboxError:
        return deny(handler, 404, "no such sandbox")
    pushing = service == "git-receive-pack" and rest != "/info/refs"
    lk = None
    if pushing:
        lk = app.sb.lock(slug, name)
        if not lk.acquire(blocking=False):
            return deny(handler, 409, "this sandbox is busy (%s) - push again when it is done" % (app.sb.busy.get((slug, name)) or "a build"))
    try:
        limit = int(app.cfg["limits"]["git_max_push_mb"]) * 2 ** 20
        body, size = (None, 0)
        if method == "POST":
            try:
                body, size = read_body(handler, limit)
            except ValueError as exc:
                return deny(handler, 413, str(exc))
        old = git(repo, "rev-parse", "-q", "--verify", "refs/heads/main", check=False).stdout.strip() if pushing else None
        if pushing:
            app.sb.busy[(slug, name)] = "a push"
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")}
        env.update(GIT_PROJECT_ROOT=repo, PATH_INFO="/.git" + rest, QUERY_STRING=query, REQUEST_METHOD=method, GIT_HTTP_EXPORT_ALL="1",
                   REMOTE_USER=who, REMOTE_ADDR=handler.client_address[0], CONTENT_TYPE=handler.headers.get("Content-Type") or "",
                   GIT_HTTP_MAX_REQUEST_BUFFER=str(limit), GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
        if method == "POST":
            env["CONTENT_LENGTH"] = str(size)
        if handler.headers.get("Content-Encoding"):
            env["HTTP_CONTENT_ENCODING"] = handler.headers["Content-Encoding"]
        if handler.headers.get("Git-Protocol"):
            env["GIT_PROTOCOL"] = handler.headers["Git-Protocol"]
        p = subprocess.run([GIT] + SAFE + ["http-backend"], stdin=body or subprocess.DEVNULL, capture_output=True, env=env, timeout=600)
        out = p.stdout
        head, sep, payload = out.partition(b"\r\n\r\n")
        if not sep:
            head, sep, payload = out.partition(b"\n\n")
        status, hdrs = 200, []
        for ln in head.decode("latin1").splitlines():
            k, _, v = ln.partition(":")
            if k.lower() == "status":
                status = int(v.strip().split()[0])
            elif k:
                hdrs.append((k.strip(), v.strip()))
        if pushing:                                   # file the push BEFORE answering, so it is listed the moment git returns
            new = git(repo, "rev-parse", "-q", "--verify", "refs/heads/main", check=False).stdout.strip()
            app.sb.busy.pop((slug, name), None)
            if new and new != old:
                if app.sb.size_mb(repo) > float(app.cfg["limits"]["sandbox_max_mb"]):
                    app.log("sandbox %s/%s is over its size limit after a push" % (slug, name))
                lk.release()
                lk = None
                app.builds.record_push(slug, who_email, name, old, new)
                app.log("push to %s/%s by %s: %s..%s" % (slug, name, who_email, (old or "-")[:10], new[:10]))
        handler.send_response(status)
        for k, v in hdrs:
            if k.lower() not in ("content-length",):
                handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
    finally:
        if pushing:
            app.sb.busy.pop((slug, name), None)
        if lk:
            lk.release()
