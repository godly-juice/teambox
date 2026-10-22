"""Home feed + weekly team feed tests. Run on Linux:   python3 -m unittest -v tests.test_feeds
Starts the REAL team box (in-process, temp data dir, random port) with the fake team gateway + fake llama, like test_teambox.
The Tuesday-14:00 clock is simulated by calling the team feed's tick(now=...) with chosen times. Never calls a real model."""
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fake_gateway import FakeGateway  # noqa: E402
from test_teambox import EXACT_BOTTOM, free_port  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox import tour  # noqa: E402
from teambox.modules import team_feed  # noqa: E402
from teambox.server import make_server  # noqa: E402

A, B, ADMIN = "alice@test.example", "bob@test.example", "marjus@test.example"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
THUMBS = "\U0001F44D"
MAX_FILE_MB, QUOTA_MB = 0.05, 0.15            # ~51 KB per file, ~153 KB per member (small, so the limits are cheap to test)


def readf(p):
    with open(p, encoding="utf8") as fh:
        return fh.read()


def make_xlsx(rows, doctype=False):
    buf = io.BytesIO()
    strings = []
    xml_rows = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, v in enumerate(row):
            ref = "%s%d" % (chr(65 + j), i)
            if isinstance(v, str):
                strings.append(v)
                cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, len(strings) - 1))
            else:
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, v))
        xml_rows.append('<row r="%d">%s</row>' % (i, "".join(cells)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    dtd = '<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">]>' if doctype else ""
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?>%s<sst %s>%s</sst>' % (dtd, ns, "".join("<si><t>%s</t></si>" % escape(s) for s in strings)))
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?>%s<worksheet %s><sheetData>%s</sheetData></worksheet>' % (dtd, ns, "".join(xml_rows)))
    return buf.getvalue()


def make_ods(rows):
    buf = io.BytesIO()
    t = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    x = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    o = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    body = "".join("<table:table-row>%s</table:table-row>" % "".join("<table:table-cell><text:p>%s</text:p></table:table-cell>" % escape(c) for c in r) for r in rows)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", '<?xml version="1.0"?><office:document-content xmlns:office="%s" xmlns:table="%s" xmlns:text="%s"><office:body>'
                                  '<office:spreadsheet><table:table table:name="S1">%s</table:table></office:spreadsheet></office:body>'
                                  '</office:document-content>' % (o, t, x, body))
    return buf.getvalue()


class FeedsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="teambox-feeds-")
        cls.secret = "test-secret-" + os.urandom(8).hex()
        sf = os.path.join(cls.tmp, "gateway.secret")
        with open(sf, "w") as fh:
            fh.write(cls.secret)
        cls.llama_port = free_port()
        cls.llama = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_llama.py"), "--port", str(cls.llama_port), "--tps", "50"])
        cls.gw = FakeGateway(cls.secret, [A, B, ADMIN], cls.llama_port)
        cls.port = free_port()
        cls.cfgp = os.path.join(cls.tmp, "config.json")
        with open(cls.cfgp, "w") as fh:
            json.dump({"port": cls.port, "data_dir": os.path.join(cls.tmp, "data"), "public_url": "http://127.0.0.1:%d" % cls.port,
                       "admin_emails": [ADMIN], "dev_mode": True,
                       "gateway": {"url": "http://127.0.0.1:%d" % cls.gw.port, "secret_file": sf, "timeout_s": 30},
                       "builds": {"max_total": 2, "max_steps": 12, "max_minutes": 5}, "jail": {"memory_mb": 512, "timeout_s": 20},
                       "feeds": {"max_file_mb": MAX_FILE_MB, "member_quota_mb": QUOTA_MB, "max_files_per_post": 4,
                                 "timezone": "America/Chicago"}}, fh)
        os.environ["TEAMBOX_ALLOW_DEV"] = "1"
        cls.cfg = C.load(cls.cfgp)
        cls.app, cls.srv = make_server(cls.cfg)            # NOTE: modules.start (the real-clock scheduler) is not called in tests
        cls.app.builds.retry_sleep = 0.2
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/healthz", timeout=1)
                urllib.request.urlopen("http://127.0.0.1:%d/health" % cls.llama_port, timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        cls.tf = cls.app.ext["team-feed"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.gw.stop()
        cls.llama.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def req(self, method, path, who=None, data=None, headers=None, csrf=True):
        h = dict(headers or {})
        if who:
            h["X-Dev-Email"] = who
        if method == "POST" and csrf:
            h["X-Teambox"] = "1"
        r = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def api(self, method, path, who=None, body=None, csrf=True):
        data = json.dumps(body or {}).encode() if method == "POST" else None
        s, _h, raw = self.req(method, path, who, data, {"Content-Type": "application/json"} if data else None, csrf)
        try:
            return s, json.loads(raw)
        except ValueError:
            return s, raw.decode("utf8", "replace")

    def upload(self, who, name, data, csrf=True):
        s, _h, raw = self.req("POST", "/api/m/feed/upload?name=" + urllib.request.quote(name), who, data,
                              {"Content-Type": "application/octet-stream"}, csrf)
        return s, json.loads(raw)

    def me(self, who):
        return self.api("GET", "/api/me", who)[1]

    def post(self, who, text, files=()):
        ids = []
        for name, data in files:
            s, j = self.upload(who, name, data)
            self.assertEqual(s, 200, j)
            ids.append(j["file"]["id"])
        s, j = self.api("POST", "/api/m/feed/posts", who, {"text": text, "attachments": ids})
        self.assertEqual(s, 200, j)
        return j["post"]

    def wait_build(self, who, bid, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s, b = self.api("GET", "/api/builds/" + bid, who)
            if b.get("status") not in ("running", "testing", "queued"):
                return b
            time.sleep(0.3)
        self.fail("build did not finish")

    def clean_files(self, who):
        """Delete every post of `who` (frees the quota between tests)."""
        for p in self.api("GET", "/api/m/feed/posts?limit=50", who)[1]["posts"]:
            if p["can_edit"]:
                self.api("POST", "/api/m/feed/post/delete", who, {"id": p["id"]})
        for f in self.api("GET", "/api/m/feed/usage", who)[1]["pending"]:
            self.assertEqual(self.api("POST", "/api/m/feed/upload/discard", who, {"id": f["id"]})[0], 200)
        self.assertEqual(self.api("GET", "/api/m/feed/usage", who)[1]["used_bytes"], 0)

    # ============================================================ 0. live + tour + public page wording
    def test_01_live_tour_and_claims(self):
        s, j = self.api("GET", "/api/modules", A)
        st = {m["id"]: m["status"] for m in j["modules"]}
        self.assertEqual((st["feed"], st["team-feed"]), ("live", "live"))
        s, t = self.api("GET", "/api/tour", A)
        self.assertEqual(t["bottom_text"], EXACT_BOTTOM, "Marjus's bottom text is unchanged")
        self.assertEqual(tour.BOTTOM_TEXT, EXACT_BOTTOM)
        cps = {c["id"]: c for c in t["checkpoints"]}
        for cid in ("home", "team-feed"):
            self.assertNotIn("coming soon", cps[cid]["text"].lower())
            self.assertNotIn(" will ", " " + cps[cid]["text"] + " ", "live areas are described as they are, not as plans")
            self.assertTrue(cps[cid]["link"].startswith("#/m/"))
        self.assertIn("Weekly team feed: LIVE", t["prompt"])
        self.assertIn("Home feed uploads: LIVE", t["prompt"])
        self.assertIn("%s MB per file and %s MB per member" % (MAX_FILE_MB, QUOTA_MB), t["prompt"], "the prompt quotes the box's real caps")
        self.assertIn("America/Chicago", t["prompt"])
        s, page = self.api("GET", "/how-it-works")
        self.assertNotIn("Team feed (every Tuesday 14:00) - not live yet", page)
        self.assertNotIn("Home feed - not live yet", page)
        for phrase in ("application/octet-stream", "X-Content-Type-Options: nosniff", "read-only copy made at 14:00", "published late"):
            self.assertIn(phrase, page)
        s, pub = self.api("GET", "/api/public/tour")
        self.assertEqual(pub["prompt"], t["prompt"])
        js = readf(os.path.join(ROOT, "static", "app.js"))
        self.assertNotIn("coming soon.\" })", js.split("function vHome")[1].split("function tile")[0], "the home slot is no longer 'coming soon'")
        print("\n  tour: home + team-feed checkpoints live (no 'coming soon'), bottom text exact, prompt claims 9 + 12 LIVE with real caps")

    # ============================================================ 1. uploads: types, sizes, quota, serving
    def test_10_upload_types_and_limits(self):
        s, j = self.upload(A, "pic.png", PNG)
        self.assertEqual((s, j["file"]["kind"], j["file"]["inline"]), (200, "picture", True), j)
        s, j = self.upload(A, "tool.exe", b"MZ" + b"\x00" * 50)
        self.assertEqual(s, 415, j)
        self.assertIn("can not be attached", j["error"])
        s, j = self.upload(A, "noext", b"hello")
        self.assertEqual(s, 415)
        s, j = self.upload(A, "fake.png", b"<html><script>alert(1)</script></html>")
        self.assertEqual(s, 415, "a web page renamed .png is refused")
        self.assertIn("does not match", j["error"])
        s, j = self.upload(A, "fake.pdf", b"PK\x03\x04 not a pdf")
        self.assertEqual(s, 415)
        s, j = self.upload(A, "big.txt", b"x" * int(MAX_FILE_MB * 2 ** 20 + 10))
        self.assertEqual(s, 413, j)
        self.assertIn("the most per file is %s MB" % MAX_FILE_MB, j["error"])
        s, j = self.upload(A, "pic.png", PNG, csrf=False)
        self.assertEqual(s, 403, "uploads need the X-Teambox header too")
        s, j = self.api("POST", "/api/m/feed/upload?name=x.png", A)   # a JSON body to the raw route: magic check refuses it
        self.assertEqual(s, 415)
        # quota: 3 x 45 KB fit under ~153 KB? 2 do, the 4th does not
        ok = 0
        for i in range(4):
            s, j = self.upload(A, "n%d.txt" % i, b"y" * 45000)
            if s == 200:
                ok += 1
            else:
                self.assertEqual(s, 413)
                self.assertIn("quota", j["error"])
                break
        self.assertTrue(1 <= ok < 4, "the per-member quota stops uploads")
        s, u = self.api("GET", "/api/m/feed/usage", A)
        self.assertLessEqual(u["used_bytes"], u["quota_bytes"])
        # a pending (not yet posted) upload is only the uploader's
        s, j = self.upload(B, "b.png", PNG)
        fid = j["file"]["id"]
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=" + fid, A)[0], 404, "A can not open B's unposted upload")
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=" + fid, B)[0], 200)
        s, j = self.api("POST", "/api/m/feed/posts", A, {"text": "steal", "attachments": [fid]})
        self.assertEqual(s, 400, "you can only attach your own uploads")
        s, j = self.api("POST", "/api/m/feed/upload/discard", A, {"id": fid})
        self.assertEqual(s, 404, "A can not discard B's upload")
        s, j = self.api("POST", "/api/m/feed/posts", B, {"text": "my pic", "attachments": [fid]})
        self.assertEqual(s, 200)
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=" + fid, A)[0], 200, "once posted, the whole team sees it")
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=" + fid)[0], 401, "never without signing in")
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=../../members.json", A)[0], 404)
        print("\n  uploads: png OK; .exe/no-ext/renamed html/fake pdf 415; >%s MB 413; quota 413 after %d files; pending private; "
              "posted visible to team; no sign-in 401" % (MAX_FILE_MB, ok))
        self.clean_files(A)
        self.clean_files(B)

    def test_11_html_svg_never_run_as_page(self):
        evil_html = b"<!doctype html><html><body><script>document.title='PWNED'</script></body></html>"
        evil_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        p = self.post(A, "markup", [("evil.html", evil_html), ("evil.svg", evil_svg), ("pic.png", PNG)])
        atts = {a["name"]: a for a in p["attachments"]}
        for n in ("evil.html", "evil.svg"):
            a = atts[n]
            self.assertEqual((a["kind"], a["inline"]), ("download-only", False))
            for url in (a["url"], a["download"], a["url"] + "&inline=1"):
                s, hd, body = self.req("GET", url, B)
                self.assertEqual(s, 200)
                self.assertEqual(hd["Content-Type"], "application/octet-stream", "never text/html or image/svg+xml")
                self.assertTrue(hd["Content-Disposition"].startswith("attachment;"), hd["Content-Disposition"])
                self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
                self.assertIn("sandbox", hd["Content-Security-Policy"])
                self.assertIn("default-src 'none'", hd["Content-Security-Policy"])
        s, hd, body = self.req("GET", atts["pic.png"]["url"], B)
        self.assertEqual((hd["Content-Type"], hd["Content-Disposition"].split(";")[0]), ("image/png", "inline"))
        self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, PNG)
        s, hd, body = self.req("GET", atts["pic.png"]["download"], B)
        self.assertTrue(hd["Content-Disposition"].startswith("attachment;"))
        s, hd, body = self.req("GET", atts["pic.png"]["url"], B, headers={"Range": "bytes=0-7"})
        self.assertEqual((s, body, hd["Content-Range"]), (206, PNG[:8], "bytes 0-7/%d" % len(PNG)), "range requests work (video seeking)")
        stored = os.listdir(os.path.join(self.cfg["data_dir"], "feed", "files"))
        self.assertTrue(all(len(f) == 32 and all(c in "0123456789abcdef" for c in f) for f in stored), "random names, no extension")
        self.assertFalse(os.path.abspath(self.cfg["data_dir"]).startswith(os.path.join(ROOT, "static")), "files live outside the web root")
        self.assertEqual(self.req("GET", "/static/../data/feed/files.json", A)[0], 404)
        print("\n  html/svg: application/octet-stream + attachment + nosniff + CSP sandbox (also with ?inline=1); png inline image/png; "
              "Range 206; stored as %d random 32-hex names outside static/" % len(stored))
        self.clean_files(A)

    def test_12_previews_are_plain_text(self):
        csv_data = b"name,score\nana,3\n<b>bob</b>,=1+1\n"
        md = b"# Title\n<script>alert(1)</script>\n**bold**\n"
        p = self.post(A, "sheets", [("s.csv", csv_data), ("n.md", md), ("t.xlsx", make_xlsx([["city", "temp"], ["Tirana", 21], ["Chicago", 9]])),
                                    ("o.ods", make_ods([["a", "b"], ["1", "<i>2</i>"]]))])
        pv = {}
        for a in p["attachments"]:
            s, j = self.api("GET", "/api/m/feed/preview?id=" + a["id"], B)
            self.assertEqual(s, 200, j)
            pv[a["name"]] = j["preview"]
        self.assertEqual(pv["s.csv"], {"type": "table", "rows": [["name", "score"], ["ana", "3"], ["<b>bob</b>", "=1+1"]]})
        self.assertEqual(pv["n.md"]["type"], "text")
        self.assertIn("<script>alert(1)</script>", pv["n.md"]["text"], "the raw text is returned as a string (the UI shows it with textContent)")
        self.assertEqual(pv["t.xlsx"]["rows"], [["city", "temp"], ["Tirana", "21"], ["Chicago", "9"]])
        self.assertEqual(pv["o.ods"]["rows"], [["a", "b"], ["1", "<i>2</i>"]])
        for a in p["attachments"]:
            s, hd, _b = self.req("GET", a["url"], B)
            self.assertTrue(hd["Content-Disposition"].startswith("attachment;"), "notes and sheets are downloads, never inline pages")
        self.clean_files(A)
        p = self.post(A, "dtd", [("bomb.xlsx", make_xlsx([["x"]], doctype=True))])
        s, j = self.api("GET", "/api/m/feed/preview?id=" + p["attachments"][0]["id"], A)
        self.assertEqual(j["preview"]["type"], "none", "an xlsx with a DTD gets no preview (no entity tricks)")
        js = readf(os.path.join(ROOT, "static", "feeds.js"))
        self.assertNotIn("innerHTML", js)
        print("\n  previews: csv/xlsx/ods first rows as strings, md as raw text, DTD xlsx -> no preview; feeds.js never uses innerHTML")
        self.clean_files(A)

    # ============================================================ 2. posts: comments, reactions, edit/delete permissions
    def test_13_posts_comments_reactions_permissions(self):
        p = self.post(A, "hello team", [("pic.png", PNG)])
        pid, fid = p["id"], p["attachments"][0]["id"]
        s, j = self.api("POST", "/api/m/feed/comment", B, {"post": pid, "text": "nice"})
        self.assertEqual(s, 200, j)
        bc = j["result"]
        s, j = self.api("POST", "/api/m/feed/comment", A, {"post": pid, "text": "thanks"})
        ac = j["result"]
        s, j = self.api("POST", "/api/m/feed/react", B, {"post": pid, "emoji": THUMBS})
        self.assertEqual(next(r for r in j["reactions"] if r["emoji"] == THUMBS), {"emoji": THUMBS, "count": 1, "mine": True})
        s, j = self.api("POST", "/api/m/feed/react", A, {"post": pid, "emoji": THUMBS})
        self.assertEqual(next(r for r in j["reactions"] if r["emoji"] == THUMBS)["count"], 2)
        s, j = self.api("POST", "/api/m/feed/react", B, {"post": pid, "emoji": THUMBS})
        self.assertEqual(next(r for r in j["reactions"] if r["emoji"] == THUMBS)["count"], 1, "a second click takes the reaction back")
        self.assertEqual(self.api("POST", "/api/m/feed/react", B, {"post": pid, "emoji": "<img src=x>"})[0], 400, "only the offered emoji")
        self.assertEqual(self.api("POST", "/api/m/feed/comment", B, {"post": pid, "text": "   "})[0], 400)
        self.assertEqual(self.api("POST", "/api/m/feed/comment/delete", B, {"post": pid, "comment": ac})[0], 403, "not B's comment")
        self.assertEqual(self.api("POST", "/api/m/feed/comment/delete", B, {"post": pid, "comment": bc})[0], 200, "own comment")
        self.assertEqual(self.api("POST", "/api/m/feed/post/edit", B, {"id": pid, "text": "hacked"})[0], 403, "only the author edits")
        self.assertEqual(self.api("POST", "/api/m/feed/post/edit", ADMIN, {"id": pid, "text": "admin edit"})[0], 403, "admins delete, they do not edit")
        self.assertEqual(self.api("POST", "/api/m/feed/post/delete", B, {"id": pid})[0], 403, "B can not delete A's post")
        s, j = self.api("POST", "/api/m/feed/post/edit", A, {"id": pid, "text": "hello team (edited)"})
        self.assertEqual((s, j["post"]["text"], bool(j["post"]["edited"])), (200, "hello team (edited)", True))
        posts = self.api("GET", "/api/m/feed/posts", B)[1]["posts"]
        mine = next(x for x in posts if x["id"] == pid)
        self.assertEqual((mine["can_edit"], mine["can_delete"]), (False, False))
        self.assertEqual([c["text"] for c in mine["comments"]], ["thanks"])
        self.assertNotIn("author_email", json.dumps(mine), "the feed shows display names, not emails")
        self.assertEqual(self.api("POST", "/api/m/feed/comment/delete", ADMIN, {"post": pid, "comment": ac})[0], 200, "admins can delete any comment")
        s, j = self.api("POST", "/api/m/feed/post/delete", ADMIN, {"id": pid})
        self.assertEqual(s, 200, "admins can delete any post")
        self.assertEqual(self.req("GET", "/api/m/feed/file?id=" + fid, A)[0], 404, "its files are gone too")
        self.assertFalse(os.path.exists(os.path.join(self.cfg["data_dir"], "feed", "files", fid)))
        self.assertEqual(self.api("GET", "/api/m/feed/usage", A)[1]["used_bytes"], 0, "the quota is freed")
        p2 = self.post(B, "bye")
        self.assertEqual(self.api("POST", "/api/m/feed/post/delete", B, {"id": p2["id"]})[0], 200, "authors delete their own")
        print("\n  posts: comments + emoji toggle; only the offered emoji; author edits (B/admin 403); B can not delete A's; "
              "admin deletes any post/comment; files + quota freed")

    # ============================================================ 3. the weekly team feed
    def test_20_private_until_tuesday(self):
        a = self.me(A)["id"]
        self.me(B)
        s, j = self.api("POST", "/api/builds", A, {"sandbox": "my-sandbox", "task": "NORMAL: add an add() function with a test", "model": "builder-bot"})
        self.assertEqual(s, 200, j)
        bid = j["build"]["id"]
        b = self.wait_build(A, bid)
        self.assertEqual(b["verdict"], "tests pass")
        self.assertEqual(self.api("POST", "/api/builds/%s/apply" % bid, A)[0], 200)          # -> a commit on A's main
        self.assertEqual(self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Terse", "settings": {"system_prompt": "Be terse.", "temperature": 0.1}})[0], 200)
        self.assertEqual(self.api("POST", "/api/sandboxes", A, {"name": "weather-x"})[0], 200)
        type(self).bid = bid
        nxt = self.tf.next_slot(time.time())
        type(self).slot = nxt
        self.assertIsNone(self.tf.tick(nxt.timestamp() - 60), "one minute before Tuesday 14:00: nothing is published")
        self.assertEqual(self.api("GET", "/api/m/team-feed/weeks", B)[1]["weeks"], [])
        self.assertEqual(self.api("GET", "/api/builds/" + bid, B)[0], 404, "B can not see A's build before Tuesday")
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/weather-x" % a, B)[0], 404)
        self.assertEqual(self.api("GET", "/api/m/team-feed/item?id=" + team_feed.iid_of("build:" + bid), B)[0], 404,
                         "the team feed has no copy of unpublished work")
        pa = self.api("GET", "/api/m/team-feed/pending", A)[1]["pending"]
        kinds = sorted({x["type"] for x in pa})
        self.assertEqual(kinds, ["build", "commits", "sandbox", "version"], pa)
        self.assertEqual([x for x in self.api("GET", "/api/m/team-feed/pending", B)[1]["pending"] if x["owner"] == a], [],
                         "B's 'goes out next time' list shows only B's own work")
        self.assertTrue(any(x["owner"] == a for x in self.api("GET", "/api/m/team-feed/pending", ADMIN)[1]["pending"]), "admins see everyone's")
        print("\n  before Tue 14:00 (%s): tick publishes nothing; B: build 404, sandbox 404, team-feed item 404; A's pending = %s"
              % (nxt.strftime("%a %Y-%m-%d %H:%M %Z"), kinds))

    def test_21_publish_catches_up_and_is_idempotent(self):
        if not hasattr(type(self), "slot"):
            self.test_20_private_until_tuesday()
        slot = type(self).slot
        late = slot.timestamp() + 2 * 86400 + 3600              # the box was off at 14:00 and came back on Thursday
        rec = self.tf.tick(late)
        self.assertIsNotNone(rec, "the missed Tuesday is published on the next tick (catch-up)")
        self.assertEqual(rec["key"], slot.strftime("%Y-%m-%d"))
        self.assertTrue(rec["late"])
        self.assertEqual(rec["cutoff"], slot.timestamp(), "cut off at Tuesday 14:00, not at the late publish time")
        self.assertEqual(sorted(rec["counts"]), ["build", "commits", "sandbox", "version"], rec["counts"])
        n_items = len(os.listdir(os.path.join(self.cfg["data_dir"], "team-feed", "items")))
        self.assertIsNone(self.tf.tick(late), "running it twice publishes once")
        self.assertIsNone(self.tf.tick(late + 3600))
        self.assertIsNone(self.tf.tick(slot.timestamp() + 30))
        self.assertEqual(len(os.listdir(os.path.join(self.cfg["data_dir"], "team-feed", "items"))), n_items)
        # two processes/threads racing on the NEXT Tuesday: exactly one publishes (thread lock + file lock + record)
        tf2 = team_feed.TeamFeed(self.cfg, self.app.store)
        nxt2 = self.tf.next_slot(late).timestamp() + 60
        res = []
        ths = [threading.Thread(target=lambda t=t: res.append(t.tick(nxt2))) for t in (self.tf, tf2, self.tf, tf2)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(sum(1 for r in res if r), 1, res)
        weeks = self.api("GET", "/api/m/team-feed/weeks", B)[1]["weeks"]
        self.assertEqual([w["key"] for w in weeks][-1], slot.strftime("%Y-%m-%d"))
        st = json.loads(readf(os.path.join(self.cfg["data_dir"], "team-feed", "publishes.json")))
        self.assertEqual(len(st["weeks"]), 2, "one record per Tuesday")
        print("\n  publish: catch-up on Thursday -> week %s (late, cutoff = Tue 14:00, %s); 3 more ticks -> nothing; 4 racing ticks -> 1 publish"
              % (rec["key"], rec["counts"]))

    def test_22_published_read_only_and_social(self):
        if not hasattr(type(self), "slot"):
            self.test_21_publish_catches_up_and_is_idempotent()
        a = self.me(A)["id"]
        bid, key = type(self).bid, type(self).slot.strftime("%Y-%m-%d")
        s, j = self.api("GET", "/api/m/team-feed/items?week=" + key, B)
        self.assertEqual(s, 200, j)
        items = {i["type"]: i for i in j["items"]}
        self.assertEqual(items["build"]["owner"], a)
        s, it = self.api("GET", "/api/m/team-feed/item?id=" + items["build"]["id"], B)
        it = it["item"]
        self.assertTrue(it["read_only"])
        self.assertIn("+def add(a, b):", it["data"]["diff"], "B sees the diff")
        self.assertIn("PASS", it["data"]["tests_text"], "and the test results")
        self.assertEqual(it["data"]["verdict"], "tests pass")
        s, v = self.api("GET", "/api/m/team-feed/item?id=" + items["version"]["id"], B)
        self.assertEqual(v["item"]["data"]["settings"], {"system_prompt": "Be terse.", "temperature": 0.1})
        s, cm = self.api("GET", "/api/m/team-feed/item?id=" + items["commits"]["id"], B)
        self.assertTrue(cm["item"]["data"]["commits"])
        self.assertIn("add.py", cm["item"]["data"]["stat"])
        # comments + reactions by everyone
        self.assertEqual(self.api("POST", "/api/m/team-feed/comment", B, {"item": it["id"], "text": "great add()"})[0], 200)
        s, j = self.api("POST", "/api/m/team-feed/react", B, {"item": it["id"], "emoji": THUMBS})
        self.assertEqual(next(r for r in j["reactions"] if r["emoji"] == THUMBS)["count"], 1)
        s, j = self.api("POST", "/api/m/team-feed/comment", A, {"item": it["id"], "text": "thanks!"})
        ac = j["result"]
        self.assertEqual(self.api("POST", "/api/m/team-feed/comment/delete", B, {"item": it["id"], "comment": ac})[0], 403)
        self.assertEqual(self.api("POST", "/api/m/team-feed/react", B, {"item": it["id"], "emoji": "x"})[0], 400)
        self.assertEqual(self.api("POST", "/api/m/team-feed/comment", B, {"item": "f" * 24, "text": "x"})[0], 404)
        s, it2 = self.api("GET", "/api/m/team-feed/item?id=" + it["id"], A)
        self.assertEqual([c["text"] for c in it2["item"]["comments"]], ["great add()", "thanks!"])
        # still NO access to the real thing
        self.assertEqual(self.api("GET", "/api/builds/" + bid, B)[0], 404, "the build page itself stays owner + admins")
        self.assertEqual(self.api("POST", "/api/builds/%s/reject" % bid, B)[0], 404)
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, B)[0], 404)
        s, tok = self.api("POST", "/api/tokens", B, {"label": "b"})
        r = subprocess.run(["git", "-c", "credential.helper=", "clone", "http://x:%s@127.0.0.1:%d/git/%s/my-sandbox.git" % (tok["token"], self.port, a),
                            os.path.join(self.tmp, "bclone")], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME=self.tmp))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("403", r.stderr, "no git access to A's sandbox after the publish either")
        # new work after the publish is private again until the next Tuesday
        self.assertEqual(self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Later", "settings": {"temperature": 0.9}})[0], 200)
        pend = [x for x in self.api("GET", "/api/m/team-feed/pending", A)[1]["pending"] if x["type"] == "version"]
        self.assertEqual(len(pend), 1, "only the new version waits; the published one is not repeated")
        print("\n  after publish: B sees A's build diff/tests, settings version, commits (read-only), both comment + react; "
              "B still 404 on /api/builds, 404 on the sandbox, git clone 403; new work private again")

    def test_23_publish_now_admin_only(self):
        if not hasattr(type(self), "slot"):
            self.test_22_published_read_only_and_social()
        self.assertEqual(self.api("POST", "/api/m/team-feed/publish-now", B, {"confirm": True})[0], 403)
        self.assertEqual(self.api("POST", "/api/m/team-feed/publish-now", ADMIN, {})[0], 400, "admin must confirm")
        s, j = self.api("POST", "/api/m/team-feed/publish-now", ADMIN, {"confirm": True})
        self.assertEqual(s, 200, j)
        self.assertTrue(j["week"]["manual"])
        self.assertGreaterEqual(j["count"], 1)
        time.sleep(1.1)
        s, j2 = self.api("POST", "/api/m/team-feed/publish-now", ADMIN, {"confirm": True})
        self.assertEqual(j2["count"], 0, "nothing is published twice")
        print("\n  publish now: member 403, admin w/o confirm 400, admin OK (%d new item(s), labelled manual); again -> 0" % j["count"])

    def test_24_clock_math_timezone_dst(self):
        tf = self.tf
        chi = team_feed.ZoneInfo("America/Chicago")
        at = lambda *a: dt.datetime(*a, tzinfo=chi).timestamp()  # noqa: E731
        self.assertEqual(tf.slot_on_or_before(at(2026, 10, 27, 13, 59)).strftime("%Y-%m-%d %H:%M"), "2026-10-20 14:00")
        self.assertEqual(tf.slot_on_or_before(at(2026, 10, 27, 14, 0)).strftime("%Y-%m-%d %H:%M"), "2026-10-27 14:00")
        self.assertEqual(tf.next_slot(at(2026, 10, 21, 9, 0)).strftime("%Y-%m-%d %H:%M"), "2026-10-27 14:00")
        s = tf.next_slot(at(2026, 10, 28, 9, 0))                                      # after the DST change (Nov 1)
        self.assertEqual(s.strftime("%Y-%m-%d %H:%M %Z"), "2026-11-03 14:00 CST")
        self.assertEqual(dt.datetime.fromtimestamp(s.timestamp(), dt.timezone.utc).strftime("%H:%M"), "20:00")
        self.assertEqual(dt.datetime.fromtimestamp(at(2026, 10, 27, 14, 0), dt.timezone.utc).strftime("%H:%M"), "19:00", "CDT before")
        r = subprocess.run([sys.executable, "-m", "teambox.modules.team_feed", "--config", self.cfgp], cwd=ROOT, capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, TEAMBOX_ALLOW_DEV="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["published"], None, "the systemd-timer CLI: nothing due right now -> publishes nothing")
        print("\n  clock: Tue 13:59 -> previous week, 14:00 -> this week; 14:00 = 19:00 UTC (CDT) and 20:00 UTC after DST (CST); "
              "CLI --tick: %s" % r.stdout.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
