"""Home feed + "Share to feed" tests. Run on Linux:   python3 -m unittest -v tests.test_feeds
Starts the REAL team box (in-process, temp data dir, random port) with the fake team gateway + fake llama, like test_teambox.
Never calls a real model. (The weekly Tuesday team feed was removed on 2026-10-21; test_23 proves no trace of it is left.)"""
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
                                 }}, fh)
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
        self.assertEqual(st, {"feed": "live", "messages": "live"})
        s, t = self.api("GET", "/api/tour", A)
        self.assertEqual(t["bottom_text"], EXACT_BOTTOM, "Marjus's bottom text is unchanged")
        self.assertEqual(tour.BOTTOM_TEXT, EXACT_BOTTOM)
        cps = {c["id"]: c for c in t["checkpoints"]}
        for cid in ("home", "projects"):
            self.assertNotIn("coming soon", cps[cid]["text"].lower())
            self.assertNotIn(" will ", " " + cps[cid]["text"] + " ", "live areas are described as they are, not as plans")
            self.assertTrue(cps[cid]["link"].startswith("#/"))
        self.assertIn("9. Share to feed: LIVE", t["prompt"])
        self.assertIn("Home feed uploads: LIVE", t["prompt"])
        self.assertIn("%s MB per file and %s MB per member" % (MAX_FILE_MB, QUOTA_MB), t["prompt"], "the prompt quotes the box's real caps")
        self.assertIn("Share to feed", cps["projects"]["text"])
        s, page = self.api("GET", "/how-it-works")
        self.assertNotIn("Home feed - not live yet", page)
        for phrase in ("application/octet-stream", "X-Content-Type-Options: nosniff", "read-only copy", "Unshare", "id=\"sharing\""):
            self.assertIn(phrase, page)
        s, pub = self.api("GET", "/api/public/tour")
        self.assertEqual(pub["prompt"], t["prompt"])
        js = readf(os.path.join(ROOT, "static", "app.js"))
        self.assertNotIn("coming soon.\" })", js.split("function vHome")[1].split("function tile")[0], "the home slot is no longer 'coming soon'")
        print("\n  tour: home + projects checkpoints live (no 'coming soon'), bottom text exact, prompt claims 9 (Share to feed) + 12 LIVE with real caps")

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

    # ============================================================ 3. private by default + "Share to feed" (the weekly team feed is gone)
    def test_20_private_until_shared(self):
        a = self.me(A)["id"]
        self.me(B)
        s, j = self.api("POST", "/api/builds", A, {"sandbox": "my-sandbox", "task": "NORMAL: add an add() function with a test", "model": "builder-bot"})
        self.assertEqual(s, 200, j)
        bid = j["build"]["id"]
        b = self.wait_build(A, bid)
        self.assertEqual(b["verdict"], "tests pass")
        self.assertEqual(self.api("POST", "/api/builds/%s/apply" % bid, A)[0], 200)          # -> a commit on A's main
        s, v = self.api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Terse", "settings": {"system_prompt": "Be terse.", "temperature": 0.1}})
        self.assertEqual(s, 200)
        type(self).bid, type(self).vid = bid, v["version"]["id"]
        self.assertEqual(self.api("GET", "/api/builds/" + bid, B)[0], 404, "B can not see A's build")
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, B)[0], 404)
        feed = json.dumps(self.api("GET", "/api/m/feed/posts?limit=50", B)[1])
        self.assertNotIn(bid, feed, "nothing of A's reaches the feed by itself")
        self.assertEqual(self.api("GET", "/api/m/feed/shares", A)[1]["shares"], {})
        # nobody can share someone else's work - same answer as "does not exist"
        self.assertEqual(self.api("POST", "/api/sandboxes", A, {"name": "only-ana-has-this"})[0], 200)
        for body in ({"kind": "build", "build": bid}, {"kind": "project", "project": "only-ana-has-this"}, {"kind": "version", "model": "chat-llama", "version": type(self).vid}):
            s, j = self.api("POST", "/api/m/feed/share", B, body)
            self.assertEqual(s, 404, (body, j))
            s, j = self.api("POST", "/api/m/feed/share", ADMIN, body)
            self.assertEqual(s, 404, "not even an admin shares a member's work for them: %s" % j)
        self.assertEqual(self.api("POST", "/api/m/feed/share", A, {"kind": "nonsense"})[0], 400)
        print("\n  private by default: B 404 on A's build + project, A's build not in the feed; B/admin can not share A's work (404)")

    def test_21_share_unshare_build(self):
        if not hasattr(type(self), "bid"):
            self.test_20_private_until_shared()
        a, bid = self.me(A)["id"], type(self).bid
        s, j = self.api("POST", "/api/m/feed/share", A, {"kind": "build", "build": bid, "text": "my first build"})
        self.assertEqual(s, 200, j)
        pid = j["post"]["id"]
        self.assertEqual(j["post"]["shared"]["kind"], "build")
        self.assertEqual(self.api("POST", "/api/m/feed/share", A, {"kind": "build", "build": bid})[0], 409, "shared once")
        self.assertEqual(self.api("GET", "/api/m/feed/shares", A)[1]["shares"], {"build:" + bid: pid})
        posts = self.api("GET", "/api/m/feed/posts?limit=50", B)[1]["posts"]
        mine = next(p for p in posts if p["id"] == pid)
        self.assertTrue(mine["shared"]["read_only"])
        self.assertNotIn("diff", mine["shared"], "the list carries a summary; the copy opens on request")
        s, full = self.api("GET", "/api/m/feed/shared?post=" + pid, B)
        self.assertEqual(s, 200, full)
        d = full["post"]["data"]
        self.assertIn("+def add(a, b):", d["diff"], "B sees the diff of the shared copy")
        self.assertIn("PASS", d["tests_text"])
        self.assertEqual(self.api("POST", "/api/m/feed/comment", B, {"post": pid, "text": "great add()"})[0], 200)
        s, j = self.api("POST", "/api/m/feed/react", B, {"post": pid, "emoji": THUMBS})
        self.assertEqual(next(r for r in j["reactions"] if r["emoji"] == THUMBS)["count"], 1)
        # sharing never grants access to the real thing
        self.assertEqual(self.api("GET", "/api/builds/" + bid, B)[0], 404, "the build page itself stays owner + admins")
        self.assertEqual(self.api("POST", "/api/builds/%s/reject" % bid, B)[0], 404)
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, B)[0], 404)
        s, tok = self.api("POST", "/api/tokens", B, {"label": "b"})
        r = subprocess.run(["git", "-c", "credential.helper=", "clone", "http://x:%s@127.0.0.1:%d/git/%s/my-sandbox.git" % (tok["token"], self.port, a),
                            os.path.join(self.tmp, "bclone")], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME=self.tmp))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("403", r.stderr, "no git access to A's project after sharing either")
        self.assertEqual(self.api("POST", "/api/m/feed/unshare", B, {"key": "build:" + bid})[0], 404, "B can not unshare A's post")
        s, j = self.api("POST", "/api/m/feed/unshare", A, {"key": "build:" + bid})
        self.assertEqual((s, j.get("removed")), (200, pid))
        self.assertNotIn(pid, [p["id"] for p in self.api("GET", "/api/m/feed/posts?limit=50", B)[1]["posts"]], "unshared = gone for everyone")
        self.assertEqual(self.api("GET", "/api/m/feed/shared?post=" + pid, B)[0], 404)
        self.assertFalse(os.path.exists(os.path.join(self.cfg["data_dir"], "feed", "shared", pid + ".json")), "the copy is deleted")
        print("\n  share build: post with read-only copy (diff, tests), comments + emoji; B still 404 on build/project, git clone 403; "
              "B can not unshare; A unshares -> post + copy gone")

    def test_22_share_project_and_version(self):
        if not hasattr(type(self), "bid"):
            self.test_20_private_until_shared()
        a = self.me(A)["id"]
        s, j = self.api("POST", "/api/m/feed/share", A, {"kind": "project", "project": "my-sandbox"})
        self.assertEqual(s, 200, j)
        d = self.api("GET", "/api/m/feed/shared?post=" + j["post"]["id"], B)[1]["post"]["data"]
        self.assertIn("add.py", d["files"])
        self.assertIn("add.py", d["stat"], "the project copy shows its changes since the starter")
        self.assertTrue(d["commits"])
        s, j2 = self.api("POST", "/api/m/feed/share", A, {"kind": "version", "model": "chat-llama", "version": type(self).vid})
        self.assertEqual(s, 200, j2)
        d2 = self.api("GET", "/api/m/feed/shared?post=" + j2["post"]["id"], B)[1]["post"]["data"]
        self.assertEqual(d2["settings"], {"system_prompt": "Be terse.", "temperature": 0.1})
        self.assertEqual(self.api("GET", "/api/sandboxes/%s/my-sandbox" % a, B)[0], 404, "still no access to the project")
        # an admin can take a shared post off the feed (like any post); the owner can also just delete it
        self.assertEqual(self.api("POST", "/api/m/feed/post/delete", ADMIN, {"id": j["post"]["id"]})[0], 200)
        self.assertEqual(self.api("POST", "/api/m/feed/unshare", A, {"key": "version:%s:chat-llama:%s" % (a, type(self).vid)})[0], 200)
        self.assertEqual(self.api("GET", "/api/m/feed/shares", A)[1]["shares"], {})
        print("\n  share project (files, commits, diff since starter) + settings version: B sees read-only copies; project still 404; removed again")

    def test_23_no_weekly_publish_left(self):
        """Marjus: 'take the weekly thing off' - no scheduler, timer, config, UI, route, tour/how-it-works/prompt text."""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "teambox", "modules", "team_feed.py")))
        for f in ("teambox-feed.service", "teambox-feed.timer"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, "systemd", f)))
        self.assertNotIn("team-feed", [m["id"] for m in self.api("GET", "/api/modules", A)[1]["modules"]])
        for path in ("/api/m/team-feed/weeks", "/api/m/team-feed/pending"):
            self.assertEqual(self.api("GET", path, A)[0], 404)
        self.assertEqual(self.api("POST", "/api/m/team-feed/publish-now", ADMIN, {"confirm": True})[0], 404)
        bad = ("team_feed", "team-feed", "publish-now", "publish_now", "Tuesday", "14:00", "publish_weekday", "publish_time", "teambox-feed")
        hits = []
        for dp, _ds, fs in os.walk(ROOT):
            if any(x in dp for x in (".git", "__pycache__", os.sep + "tests", os.sep + "data", os.sep + "out")):
                continue
            for f in fs:
                if f.endswith((".py", ".js", ".html", ".json", ".md", ".service", ".timer", ".css", ".txt", ".example")):
                    for ln in readf(os.path.join(dp, f)).splitlines():
                        if any(x in ln for x in ("remov", "no longer", "older version", "gone")):
                            continue                     # upgrade notes that say how to take the old service away
                        for w in bad:
                            if w in ln:
                                hits.append("%s: %s" % (os.path.relpath(os.path.join(dp, f), ROOT), w))
        self.assertEqual(hits, [], "no weekly-publish code paths or text remain")
        t = self.api("GET", "/api/tour", A)[1]
        for w in ("Tuesday", "weekly", "14:00"):
            self.assertNotIn(w, json.dumps(t))
        print("\n  weekly publish gone: module/timer files absent, routes 404, no 'Tuesday/14:00/weekly/team-feed' anywhere outside tests/")

if __name__ == "__main__":
    unittest.main(verbosity=2)
