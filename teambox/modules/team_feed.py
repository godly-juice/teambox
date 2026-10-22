"""The weekly TEAM FEED (Marjus, 2026-10-21: "every week, on tuesday, at 2pm all work and models created needs to become public
within the team so everyone can see what everyone did with a comment button so we can comment on the work and emoji reaction button
to the work as well. that becomes team's feed.").

What happens: every Tuesday at 14:00 (team box local time; config feeds.timezone, default America/Chicago) everything members made
before that moment and not yet published becomes a feed item that the whole team can see, comment on and react to:
  - builds (AI builds and pushes from their own AI tool) that have finished: task, summary, changed files, diff, tests
  - commits on their sandboxes' main branch (list + diff)
  - new sandboxes (other than the automatic "my-sandbox")
  - settings versions they saved for a model
Until then a member's work stays private (owner + admins) exactly as before. A published item is a READ-ONLY SNAPSHOT taken at
publish time and stored under data/team-feed/items/ - publishing never opens anyone's sandbox, builds API or git remote to others.

Scheduling: a thread in the app checks every minute (and once at start-up, which is the catch-up when the box was off at 14:00).
Optional instead/also: the systemd timer in systemd/teambox-feed.timer runs  python3 -m teambox.modules.team_feed --tick .
Idempotent: each week's publish is recorded in data/team-feed/publishes.json under the Tuesday's date and done once, under a
thread lock AND a file lock (so the app and the timer can not both publish). Every item has a stable key and is published only once.
Admins can "publish now" (for testing): that publishes what exists right now, and Tuesday then publishes only what is new."""
import datetime as _dt
import hashlib
import json
import os
import threading
import time

from .. import store as S
from . import social

try:
    import fcntl
except ImportError:                     # Windows (dev only): the thread lock still makes it idempotent inside one process
    fcntl = None
try:
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover
    ZoneInfo = None

MODULE = {
    "id": "team-feed",
    "title": "Team feed (weekly)",
    "nav": "Team feed",
    "status": "live",
    "summary": "Every Tuesday at 14:00 everyone's work and model versions become visible to the whole team, with comments and emoji reactions.",
    "help": "Every Tuesday at 2 pm, all work and models created that week become public within the team, so everyone can see what everyone "
            "did, comment on it and react with an emoji. Until then your builds are visible only to you and the admins. Published work "
            "is a read-only copy: nobody else gets access to your sandbox or its git remote.",
}

DEFAULTS = {"timezone": "America/Chicago", "publish_weekday": 1, "publish_time": "14:00", "scheduler": True, "tick_s": 60,
            "comment_max_chars": 4000}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UNFINISHED = ("running", "testing", "queued")
DIFF_MAX = 200000
LOCK = threading.RLock()


def fail(code, msg):
    from ..server import HttpError
    raise HttpError(code, msg)


def conf(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("feeds") or {}).items() if k in DEFAULTS})
    return out


def epoch_of(stamp, fallback=None):
    """The app writes times as local 'YYYY-MM-DD HH:MM:SS' (time.strftime) - read them back the same way."""
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return fallback


def iid_of(key):
    return hashlib.sha256(key.encode("utf8")).hexdigest()[:24]


def _git(repo, *args):
    from ..sandboxes import git
    r = git(repo, *args, check=False)
    return r.stdout if r.returncode == 0 else ""


class TeamFeed:
    def __init__(self, cfg, store, log=None):
        self.cfg, self.store, self.log = cfg, store, log or (lambda m: None)
        self.dir = store.p("team-feed")
        os.makedirs(os.path.join(self.dir, "items"), exist_ok=True)
        self.state_f = os.path.join(self.dir, "publishes.json")
        with LOCK:
            st = self.state()
            if not st.get("enabled_at"):
                st["enabled_at"] = time.time()               # no catch-up for Tuesdays before the feed existed
                self.save_state(st)

    # ------------------------------------------------------------ state
    @property
    def c(self):
        return conf(self.cfg)

    def state(self):
        st = S.jread(self.state_f, {}) or {}
        st.setdefault("weeks", {})
        st.setdefault("published", {})
        st.setdefault("heads", {})
        return st

    def save_state(self, st):
        S.jwrite(self.state_f, st)

    def name(self, slug):
        pr = self.store.profile(slug) if S.SLUG_RX.match(slug or "") else None
        return (pr or {}).get("display_name") or slug

    # ------------------------------------------------------------ the clock
    def tz(self):
        if ZoneInfo is None:
            return _dt.timezone.utc
        try:
            return ZoneInfo(self.c["timezone"])
        except Exception:  # noqa: BLE001
            self.log("team feed: unknown timezone %r - using UTC" % self.c["timezone"])
            return _dt.timezone.utc

    def _slot_on(self, d):
        hh, mm = (int(x) for x in str(self.c["publish_time"]).split(":"))
        return _dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=self.tz())

    def slot_on_or_before(self, now):
        tz = self.tz()
        local = _dt.datetime.fromtimestamp(now, tz)
        d = local.date() - _dt.timedelta(days=(local.weekday() - int(self.c["publish_weekday"])) % 7)
        s = self._slot_on(d)
        if s.timestamp() > now:
            s = self._slot_on(d - _dt.timedelta(days=7))
        return s

    def next_slot(self, now):
        return self._slot_on(self.slot_on_or_before(now).date() + _dt.timedelta(days=7))

    def schedule_text(self):
        return "every %s at %s (%s)" % (DAYS[int(self.c["publish_weekday"])], self.c["publish_time"], self.c["timezone"])

    # ------------------------------------------------------------ what exists (unpublished, finished, made before the cutoff)
    def collect(self, cutoff, st, only=None):
        out = []
        members = [v["slug"] for v in self.store.members().values()]
        if only:
            members = [m for m in members if m == only]
        # builds
        bdir = self.store.p("builds")
        if os.path.isdir(bdir):
            for f in sorted(os.listdir(bdir)):
                if not f.endswith(".json"):
                    continue
                r = S.jread(os.path.join(bdir, f))
                if not r or r.get("owner") not in members:
                    continue
                key = "build:" + r["id"]
                t = epoch_of(r.get("created"), os.path.getmtime(os.path.join(bdir, f)))
                if key in st["published"] or t >= cutoff or r.get("status") in UNFINISHED:
                    continue
                out.append((key, r["owner"], t, "build", lambda r=r: self.snap_build(r)))
        for slug in members:
            # settings versions
            for model, lst in sorted((S.jread(self.store.mdir(slug, "versions.json"), {}) or {}).items()):
                for v in lst:
                    key = "version:%s:%s:%s" % (slug, model, v["id"])
                    t = epoch_of(v.get("created"), 0)
                    if key not in st["published"] and t < cutoff:
                        out.append((key, slug, t, "version", lambda m=model, v=v: {"title": "Settings version '%s' for %s" % (v["name"], m),
                                                                                    "model": m, "name": v["name"], "settings": v["settings"],
                                                                                    "created": v.get("created")}))
            # sandboxes + their commits
            root = self.store.p("sandboxes", slug)
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if not os.path.isdir(os.path.join(p, ".git")):
                    continue
                meta = S.jread(os.path.join(root, name + ".meta.json"), {}) or {}
                key = "sandbox:%s/%s" % (slug, name)
                t = epoch_of(meta.get("created"), 0)
                if name != "my-sandbox" and key not in st["published"] and t < cutoff:
                    out.append((key, slug, t, "sandbox", lambda p=p, meta=meta, name=name: {
                        "title": "New sandbox '%s'" % (meta.get("label") or name), "sandbox": name, "label": meta.get("label") or name,
                        "created": meta.get("created"), "files": [x for x in _git(p, "ls-files").splitlines() if x][:500]}))
                c = self.commits(p, "%s/%s" % (slug, name), cutoff, st)
                if c:
                    key = "commits:%s/%s:%s" % (slug, name, c["head"])
                    if key not in st["published"]:
                        out.append((key, slug, c["t"], "commits", lambda c=c, name=name: dict(c, title="%d commit(s) in '%s'" % (len(c["commits"]), name),
                                                                                              sandbox=name)))
        return out

    def starter_sha(self):
        return _git(self.store.p("starter.git"), "rev-parse", "main").strip()

    def commits(self, p, sid, cutoff, st):
        base = st["heads"].get(sid) or self.starter_sha()
        rows = []
        for ln in _git(p, "log", "--format=%H\x1f%ct\x1f%an\x1f%s", "main", "^" + base if base else "main").splitlines():
            parts = ln.split("\x1f")
            if len(parts) == 4 and int(parts[1]) < cutoff:
                rows.append(parts)
        if not rows:
            return None
        head = rows[0][0]
        diff = _git(p, "diff", base, head) if base else ""
        return {"head": head, "base": base, "t": float(rows[0][1]),
                "commits": [{"sha": r[0][:10], "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(int(r[1]))), "author": r[2][:80],
                             "subject": r[3][:200]} for r in rows[:50]],
                "stat": _git(p, "diff", "--stat", base, head)[-20000:] if base else "",
                "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX}

    def snap_build(self, r):
        d = self.store.p("builds", r["id"])

        def rd(f):
            try:
                with open(os.path.join(d, f), encoding="utf8", errors="replace") as fh:
                    return fh.read(DIFF_MAX + 1)
            except OSError:
                return ""
        diff = rd("diff.patch")
        return {"title": r.get("title") or r["id"], "build": r["id"], "kind": r.get("kind"), "sandbox": r.get("sandbox"),
                "task": (r.get("task") or "")[:8000], "model": r.get("model"), "status": r.get("status"), "verdict": r.get("verdict"),
                "summary": r.get("summary"), "files": r.get("files") or [], "tests": r.get("tests") or [], "flags": r.get("flags") or [],
                "tests_text": rd("tests.txt")[:60000], "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX,
                "created": r.get("created"), "promoted": r.get("status") == "promoted"}

    # ------------------------------------------------------------ publishing
    def _flock(self):
        fh = open(os.path.join(self.dir, "publish.lock"), "a+")
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    def _publish(self, key, cutoff, now, manual=False, by=None, slot=None):
        """Must hold LOCK + the file lock. Writes the snapshots, then the record (so a crash half-way re-runs cleanly)."""
        st = self.state()
        found = self.collect(cutoff, st)
        iids, counts = [], {}
        for skey, owner, t, typ, snap in sorted(found, key=lambda x: x[2]):
            iid = iid_of(skey)
            data = snap()
            item = {"id": iid, "key": skey, "type": typ, "owner": owner, "title": data.pop("title", typ), "created": data.get("created"),
                    "week": key, "published": S.now_s(), "data": data, "comments": [], "reactions": {}}
            old = S.jread(os.path.join(self.dir, "items", iid + ".json"))
            if old:                                          # crash-rerun: keep comments made in between (can not happen twice normally)
                item["comments"], item["reactions"] = old.get("comments", []), old.get("reactions", {})
            S.jwrite(os.path.join(self.dir, "items", iid + ".json"), item)
            iids.append(iid)
            counts[typ] = counts.get(typ, 0) + 1
            st["published"][skey] = iid
            if typ == "commits":
                st["heads"][skey.split(":", 1)[1].rsplit(":", 1)[0]] = data["head"]
        rec = {"key": key, "cutoff": cutoff, "cutoff_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(cutoff)),
               "slot": slot.isoformat() if slot else None, "published_at": now, "published_text": S.now_s(), "manual": manual, "by": by,
               "late": bool(slot) and now - slot.timestamp() > 600, "items": iids, "counts": counts}
        st["weeks"][key] = rec
        self.save_state(st)
        self.log("team feed: published %s (%d items%s%s)" % (key, len(iids), ", manual by %s" % by if manual else "",
                                                            ", catch-up" if rec["late"] else ""))
        return rec

    def tick(self, now=None):
        """Publish the latest Tuesday-14:00 slot if it is due and not done yet. Safe to call any number of times."""
        now = time.time() if now is None else now
        slot = self.slot_on_or_before(now)
        key = slot.strftime("%Y-%m-%d")
        with LOCK:
            st = self.state()
            if key in st["weeks"] or slot.timestamp() < float(st.get("enabled_at") or 0):
                return None
            fh = self._flock()
            try:
                if key in self.state()["weeks"]:              # the other process (timer / app) got there first
                    return None
                return self._publish(key, slot.timestamp(), now, slot=slot)
            finally:
                fh.close()

    def publish_now(self, by, now=None):
        now = time.time() if now is None else now
        with LOCK:
            fh = self._flock()
            try:
                return self._publish("manual-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), now, now, manual=True, by=by)
            finally:
                fh.close()

    # ------------------------------------------------------------ reading (everyone signed in sees PUBLISHED items only)
    def item(self, iid):
        if not iid or len(iid) != 24 or not all(ch in "0123456789abcdef" for ch in iid):
            return None
        return S.jread(os.path.join(self.dir, "items", iid + ".json"))

    def summary(self, it, slug):
        d = it.get("data") or {}
        v = {"id": it["id"], "type": it["type"], "owner": it["owner"], "owner_name": self.name(it["owner"]), "title": it["title"],
             "created": it.get("created"), "week": it["week"], "status": d.get("status"), "verdict": d.get("verdict"),
             "comment_count": len(it.get("comments") or [])}
        v.update(social.public_view(it, slug, self.name))
        v.pop("comments")
        return v


class Routes:
    def __init__(self, app, tf):
        self.app, self.tf = app, tf

    def weeks(self, q):
        st = self.tf.state()
        now = time.time()
        wk = sorted(st["weeks"].values(), key=lambda r: r["published_at"], reverse=True)
        return 200, {"weeks": [{k: r.get(k) for k in ("key", "cutoff_text", "published_text", "manual", "late", "counts")} | {"count": len(r["items"])}
                               for r in wk],
                     "schedule": self.tf.schedule_text(), "next_publish": self.tf.next_slot(now).strftime("%a %d %b %Y %H:%M %Z"),
                     "timezone": self.tf.c["timezone"], "emoji": social.EMOJI, "admin": q.admin}

    def items(self, q):
        st = self.tf.state()
        rec = st["weeks"].get(q.q.get("week") or "")
        if not rec:
            fail(404, "no such week")
        out = [self.tf.summary(it, q.slug) for it in (self.tf.item(i) for i in rec["items"]) if it]
        return 200, {"week": {k: rec.get(k) for k in ("key", "cutoff_text", "published_text", "manual", "late", "counts")}, "items": out}

    def item(self, q):
        it = self.tf.item(q.q.get("id") or "")
        if not it:
            fail(404, "no such item")                        # unpublished work does not exist here - for anyone
        out = self.tf.summary(it, q.slug)
        out.update(social.public_view(it, q.slug, self.tf.name))
        out["data"] = it["data"]
        out["read_only"] = True
        return 200, {"item": out}

    def _social(self, q, fn):
        with LOCK:
            it = self.tf.item(str(q.body.get("item") or ""))
            if not it:
                fail(404, "no such item")
            try:
                res = fn(it)
            except social.SocialError as exc:
                fail(exc.code, exc.msg)
            S.jwrite(os.path.join(self.tf.dir, "items", it["id"] + ".json"), it)
        return 200, dict(social.public_view(it, q.slug, self.tf.name), result=res)

    def comment(self, q):
        return self._social(q, lambda it: social.add_comment(it, q.slug, q.email, q.body.get("text"), int(self.tf.c["comment_max_chars"]))["id"])

    def comment_delete(self, q):
        return self._social(q, lambda it: social.delete_comment(it, str(q.body.get("comment") or ""), q.slug, q.admin))

    def react(self, q):
        return self._social(q, lambda it: social.toggle_reaction(it, q.slug, str(q.body.get("emoji") or "")))

    def pending(self, q):
        """What WILL be published next Tuesday: your own (admins: everyone's). Titles only."""
        st = self.tf.state()
        now = time.time()
        found = self.tf.collect(self.tf.next_slot(now).timestamp(), st, only=None if q.admin else q.slug)
        return 200, {"pending": [{"type": typ, "owner": owner, "owner_name": self.tf.name(owner), "key": key, "title": snap().get("title", typ)}
                                 for key, owner, t, typ, snap in sorted(found, key=lambda x: x[2])],
                     "next_publish": self.tf.next_slot(now).strftime("%a %d %b %Y %H:%M %Z"),
                     "note": "Unfinished builds wait for the next Tuesday after they finish."}

    def publish_now(self, q):
        if not q.admin:
            fail(403, "admins only")
        if q.body.get("confirm") is not True:
            fail(400, "confirm:true is required - this makes everything that exists now visible to the whole team")
        rec = self.tf.publish_now(q.email)
        return 200, {"week": {k: rec.get(k) for k in ("key", "cutoff_text", "published_text", "manual", "counts")}, "count": len(rec["items"])}


def init(app):
    app.ext["team-feed"] = TeamFeed(app.cfg, app.store, app.log)


def routes(app):
    r = Routes(app, app.ext["team-feed"])
    return {("GET", "/weeks"): r.weeks, ("GET", "/items"): r.items, ("GET", "/item"): r.item, ("POST", "/comment"): r.comment,
            ("POST", "/comment/delete"): r.comment_delete, ("POST", "/react"): r.react, ("GET", "/pending"): r.pending,
            ("POST", "/publish-now"): r.publish_now}


def start(app):
    tf = app.ext["team-feed"]
    if not tf.c["scheduler"]:
        return

    def loop():
        while True:
            try:
                tf.tick()                                    # the first tick at start-up is the catch-up
            except Exception as exc:  # noqa: BLE001
                app.log("team feed tick failed: %r" % exc)
            time.sleep(max(5, int(tf.c["tick_s"])))
    threading.Thread(target=loop, daemon=True, name="team-feed-scheduler").start()


def main(argv=None):
    """CLI for the systemd timer:  python3 -m teambox.modules.team_feed --config /etc/teambox/config.json --tick"""
    import sys
    from .. import config as C
    a = list(argv if argv is not None else sys.argv[1:])
    cfg = C.load(a[a.index("--config") + 1] if "--config" in a else None)
    store = S.Store(cfg["data_dir"])

    def log(m):
        with open(store.p("teambox.log"), "a", encoding="utf8") as fh:
            fh.write("%s %s\n" % (S.now_s(), m))
    tf = TeamFeed(cfg, store, log)
    rec = tf.tick()
    print(json.dumps({"published": rec["key"], "items": len(rec["items"])} if rec else {"published": None,
                     "next": tf.next_slot(time.time()).isoformat()}))


if __name__ == "__main__":
    main()
