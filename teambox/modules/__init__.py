"""EXTENSION SLOTS. Each module in this package exports:
    MODULE = {"id", "title", "nav", "status": "coming soon" | "live", "summary", "help"}
    optional: init(app)        called once when the routes are mounted (keep per-app state in app.ext[<id>])
    optional: routes(app) -> {(method, path): handler(req)}   (req = server.Req; return (code, obj)) - JSON in, JSON out
    optional: raw_routes(app) -> {(method, path): handler(http_handler, req)}   the body is NOT read (uploads, file downloads):
              the handler reads/writes the HTTP itself. Identity + the X-Teambox header on POST are still checked by the server.
    optional: start(app)       background work; called by server.main only, never by the tests
The server lists every module in the nav (GET /api/modules) and mounts its routes under /api/m/<id>/... when status is "live".
To build one: fill in its file, set status "live", add routes, add its view to static/app.js (window.TB.views[<id>]).
(2026-10-21: the scheduled team feed was removed - work reaches the home feed only through the owner's "Share to feed".)"""
from . import feed, messages

ALL = [feed, messages]


def registry():
    return [m.MODULE for m in ALL]


def _live():
    return [m for m in ALL if m.MODULE.get("status") == "live"]


def routes(app):
    out = {}
    if not hasattr(app, "ext"):
        app.ext = {}
    for m in _live():
        if hasattr(m, "init") and m.MODULE["id"] not in app.ext:
            m.init(app)
        if hasattr(m, "routes"):
            for (meth, sub), fn in m.routes(app).items():
                out[(meth, "/api/m/%s%s" % (m.MODULE["id"], sub))] = fn
    return out


def raw_route(app, method, path):
    """-> handler(http_handler, req) for a module's raw route, or None."""
    if not path.startswith("/api/m/"):
        return None
    cache = getattr(app, "_raw_routes", None)
    if cache is None:
        cache = {}
        for m in _live():
            if hasattr(m, "raw_routes"):
                for (meth, sub), fn in m.raw_routes(app).items():
                    cache[(meth, "/api/m/%s%s" % (m.MODULE["id"], sub))] = fn
        app._raw_routes = cache
    return cache.get((method, path))


def start(app):
    for m in _live():
        if hasattr(m, "start"):
            m.start(app)
