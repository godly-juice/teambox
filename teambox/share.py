"""SHARE TO FEED (the owner, 2026-10-21: "their projects shouldnt be shared actually unless they press a share button to feed. take the
weekly thing off").

Projects (sandboxes), builds and settings versions are PRIVATE: only the owner and the admins can see them. The OWNER can press
"Share to feed" on one specific project, build or version. That posts a READ-ONLY COPY (a snapshot taken at that moment) to the home
feed, where the team can comment and react. "Unshare" removes the post and its copy. Sharing never opens the project itself, the
builds API or the git remote to anyone: those keep answering "no such ..." / 403 to everyone but the owner and the admins.
This file only makes the snapshots; the posts live in modules/feed.py."""
import os

from . import store as S

DIFF_MAX = 200000
UNFINISHED = ("running", "testing", "queued")
KINDS = ("project", "build", "version")


class ShareError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _git(repo, *args):
    from .sandboxes import git
    r = git(repo, *args, check=False)
    return r.stdout if r.returncode == 0 else ""


def snap_build(store, r):
    d = store.p("builds", r["id"])

    def rd(f):
        try:
            with open(os.path.join(d, f), encoding="utf8", errors="replace") as fh:
                return fh.read(DIFF_MAX + 1)
        except OSError:
            return ""
    diff = rd("diff.patch")
    return {"title": "Build: %s" % (r.get("title") or r["id"]), "build": r["id"], "kind": r.get("kind"), "sandbox": r.get("sandbox"),
            "task": (r.get("task") or "")[:8000], "model": r.get("model"), "status": r.get("status"), "verdict": r.get("verdict"),
            "summary": r.get("summary"), "files": r.get("files") or [], "tests": r.get("tests") or [], "flags": r.get("flags") or [],
            "tests_text": rd("tests.txt")[:60000], "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX,
            "created": r.get("created"), "promoted": r.get("status") == "promoted"}


def snap_project(sb, slug, name):
    p = sb.path(slug, name)
    meta = S.jread(os.path.join(sb.root, slug, name + ".meta.json"), {}) or {}
    base = sb.starter_sha.strip() if isinstance(sb.starter_sha, str) else ""
    rows = []
    for ln in _git(p, "log", "-n", "50", "--format=%H\x1f%ct\x1f%an\x1f%s", "main").splitlines():
        parts = ln.split("\x1f")
        if len(parts) == 4:
            rows.append(parts)
    diff = _git(p, "diff", base, "main") if base else ""
    import time
    return {"title": "Project: %s" % (meta.get("label") or name), "sandbox": name, "label": meta.get("label") or name,
            "created": meta.get("created"), "files": [x for x in _git(p, "ls-files").splitlines() if x][:500],
            "head": _git(p, "rev-parse", "main").strip(),
            "commits": [{"sha": r[0][:10], "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(int(r[1]))), "author": r[2][:80],
                         "subject": r[3][:200]} for r in rows],
            "stat": _git(p, "diff", "--stat", base, "main")[-20000:] if base else "",
            "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX}


def snapshot(app, slug, body):
    """-> (key, snapshot dict) for something the member `slug` OWNS. Anything else: ShareError 404 (same as "does not exist")."""
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        raise ShareError(400, "share what? kind must be project, build or version")
    if kind == "project":
        name = str(body.get("project") or body.get("sandbox") or "")
        if not app.sb.exists(slug, name):
            raise ShareError(404, "no such project of yours")
        return "project:%s/%s" % (slug, name), snap_project(app.sb, slug, name)
    if kind == "build":
        from .sandboxes import SandboxError
        bid = str(body.get("build") or "")
        try:
            r = app.builds.get(bid)
        except SandboxError:
            raise ShareError(404, "no such build of yours")
        if r.get("owner") != slug:
            raise ShareError(404, "no such build of yours")
        if r.get("status") in UNFINISHED:
            raise ShareError(409, "this build is still %s - share it when it has finished" % r.get("status"))
        return "build:%s" % bid, snap_build(app.store, r)
    model, vid = str(body.get("model") or ""), str(body.get("version") or "")
    v = next((x for x in app.store.versions(slug).get(model, []) if x["id"] == vid), None)
    if not v:
        raise ShareError(404, "no such settings version of yours")
    return "version:%s:%s:%s" % (slug, model, vid), {"title": "Settings version '%s' for %s" % (v["name"], model), "model": model,
                                                     "name": v["name"], "settings": v["settings"], "created": v.get("created")}
