"""EXTENSION SLOTS for the next agents. Each module in this package exports:
    MODULE = {"id", "title", "nav", "status": "coming soon" | "live", "summary", "help"}
    optional: routes(app) -> {(method, path): handler(app, req)}   (req = server.Request; return (code, obj))
The server lists every module in the nav (GET /api/modules) and mounts its routes under /api/m/<id>/... when status is "live".
To build one: fill in its file, set status "live", add routes, add its view to static/app.js (window.TB.views[<id>])."""
from . import feed, messages, team_feed

ALL = [feed, team_feed, messages]


def registry():
    return [m.MODULE for m in ALL]


def routes(app):
    out = {}
    for m in ALL:
        if m.MODULE.get("status") == "live" and hasattr(m, "routes"):
            for (meth, sub), fn in m.routes(app).items():
                out[(meth, "/api/m/%s%s" % (m.MODULE["id"], sub))] = fn
    return out
