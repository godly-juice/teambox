"""Dev tool: screenshots of the home feed, Share to feed, sign-in/join, admin invites, Lumi's welcome and the vault (fake
gateway/model, temp data, headless chromium).   python3 tests/screenshots_feeds.py <out_dir>
Never touches a real model or the real box."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["TEAMBOX_ALLOW_DEV"] = "1"
from fake_gateway import FakeGateway  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox.server import make_server  # noqa: E402
from test_teambox import free_port  # noqa: E402

A, BEN, ADMIN = "ana@team.example", "ben@team.example", "marjus@team.example"


def png(w=480, h=270):
    rows = b""
    for y in range(h):
        rows += b"\x00" + b"".join(struct.pack("BBB", int(40 + 180 * x / w), int(90 + 120 * y / h), 200 - int(120 * x / w)) for x in range(w))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def main(out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tb-feed-shots-")
    sf = os.path.join(tmp, "s")
    open(sf, "w").write("sec")
    lp = free_port()
    llama = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_llama.py"), "--port", str(lp), "--tps", "60"])
    gw = FakeGateway("sec", [A, BEN, ADMIN, "liri-hoxha@team.xseed.ing"], lp)
    port = free_port()
    cp = os.path.join(tmp, "c.json")
    json.dump({"port": port, "data_dir": os.path.join(tmp, "data"), "public_url": "https://team.xseed.ing", "admin_emails": [ADMIN], "dev_mode": True, "welcome": {"model": "chat-llama"},
               "gateway": {"url": "http://127.0.0.1:%d" % gw.port, "secret_file": sf}, "jail": {"memory_mb": 512, "timeout_s": 20}}, open(cp, "w"))
    app, srv = make_server(C.load(cp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    time.sleep(1)

    def api(m, p, who, body=None):
        r = urllib.request.Request(base + p, json.dumps(body or {}).encode() if m == "POST" else None,
                                   {"X-Dev-Email": who, "X-Teambox": "1", "Content-Type": "application/json"}, method=m)
        return json.loads(urllib.request.urlopen(r, timeout=60).read())

    def up(who, name, data):
        r = urllib.request.Request(base + "/api/m/feed/upload?name=" + urllib.request.quote(name), data, {"X-Dev-Email": who, "X-Teambox": "1"}, method="POST")
        return json.loads(urllib.request.urlopen(r, timeout=60).read())["file"]["id"]

    api("POST", "/api/profile", A, {"display_name": "Ana"})
    api("POST", "/api/profile", BEN, {"display_name": "Ben"})
    api("POST", "/api/profile", ADMIN, {"display_name": "Marjus"})
    for who in (A, BEN, ADMIN):
        api("POST", "/api/tour", who, {"seen": True})
    # Ana's work: private until she presses "Share to feed"
    b = api("POST", "/api/builds", A, {"sandbox": "my-sandbox", "task": "NORMAL: add an add() function with a unit test", "model": "builder-bot"})["build"]["id"]
    for _ in range(100):
        if api("GET", "/api/builds/" + b, A)["status"] not in ("running", "testing"):
            break
        time.sleep(0.3)
    api("POST", "/api/builds/%s/apply" % b, A)
    v = api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Short answers", "settings": {"system_prompt": "Answer in one sentence.", "temperature": 0.2}})
    api("POST", "/api/sandboxes", A, {"name": "weather-bot", "label": "Weather bot"})
    sp = api("POST", "/api/m/feed/share", A, {"kind": "build", "build": b, "text": "My first AI build - add() with a test. Feedback welcome!"})["post"]["id"]
    api("POST", "/api/m/feed/share", A, {"kind": "version", "model": "chat-llama", "version": v["version"]["id"]})
    api("POST", "/api/m/feed/comment", BEN, {"post": sp, "text": "Nice and small. Could add() also take floats?"})
    api("POST", "/api/m/feed/comment", A, {"post": sp, "text": "Yes, next."})
    api("POST", "/api/m/feed/react", BEN, {"post": sp, "emoji": "\U0001F525"})
    api("POST", "/api/m/feed/react", ADMIN, {"post": sp, "emoji": "\U0001F44D"})
    # an invited account (Lumi's welcome) + open invites for the admin page
    from teambox import welcome as W
    inv, code = app.auth.create_invite("Liri Hoxha", "build the sales dashboard for our shop", "+14025550188", "liri@example.com", by="lumi")
    ticket = app.auth.redeem(code, "203.0.113.5")[1]
    acc, _tok = app.auth.complete_setup(ticket, "liri", "58203917", "203.0.113.5")
    ls = app.store.slug_for(acc["id"])
    pr = app.store.ensure_profile(ls, acc["id"])
    pr.update(display_name="Liri", setup={"vault_later": True, "profile": "skipped"})
    app.store.save_profile(ls, pr)
    W.ensure_first_project(app, ls, acc["purpose"])
    wc = W.start(app, ls, acc["id"])
    app.auth.create_invite("Dren Krasniqi", "test the mobile app and report bugs", None, "dren@example.com", by="lumi")
    LIRI = acc["id"]
    # home feed posts
    p1 = api("POST", "/api/m/feed/posts", BEN, {"text": "Scores from Saturday's test run - spreadsheet attached. Notes in the .md.",
                                                "attachments": [up(BEN, "scores.csv", b"member,builds,tests passing\nAna,3,3\nBen,2,1\nLiri,4,4\n"),
                                                                up(BEN, "notes.md", b"# Saturday\n- jail held\n- **next**: floats in add()\n"),
                                                                up(BEN, "report.pdf", b"%PDF-1.4\n%fake\n")]})["post"]["id"]
    p2 = api("POST", "/api/m/feed/posts", A, {"text": "Our new team banner draft \U0001F3A8 - what do you think?", "attachments": [up(A, "banner.png", png())]})["post"]["id"]
    api("POST", "/api/m/feed/comment", BEN, {"post": p2, "text": "Love the colours."})
    api("POST", "/api/m/feed/comment", ADMIN, {"post": p2, "text": "Ship it."})
    for who, e in ((BEN, "❤️"), (ADMIN, "❤️"), (ADMIN, "\U0001F389")):
        api("POST", "/api/m/feed/react", who, {"post": p2, "emoji": e})
    api("POST", "/api/m/feed/react", A, {"post": p1, "emoji": "\U0001F44D"})
    shots = [
        ("01-home-feed-phone-light", A, "/", "390,2600", 1), ("02-home-feed-phone-dark", A, "/", "390,2600", 0),
        ("03-home-feed-desktop-light", BEN, "/", "1280,1900", 1), ("04-home-feed-desktop-dark", BEN, "/#/m/feed", "1280,1900", 0),
        ("05-project-share-button-phone-light", A, "/#/project/%s/my-sandbox" % api("GET", "/api/me", A)["id"], "390,1500", 1),
        ("06-build-share-desktop-dark", A, "/#/build/" + b, "1280,1400", 0),
        ("07-signin-phone-light", None, "/", "390,900", 1), ("08-join-phone-dark", None, "/join", "390,900", 0),
        ("09-admin-invites-desktop-light", ADMIN, "/#/admin/invites", "1280,1400", 1),
        ("12-lumi-welcome-phone-light", LIRI, "/#/chat/" + wc["id"], "390,1600", 1),
        ("13-vault-locked-phone-dark", LIRI, "/#/vault", "390,900", 0),
        ("10-tour-phone-light", A, "/#/tour", "390,3600", 1), ("11-how-it-works-desktop-dark", A, "/how-it-works", "1280,4200", 0),
    ]
    for name, who, path, size, light in shots:
        ud = tempfile.mkdtemp(prefix="chr-")
        url = ("%s/dev/login?email=%s&next=%s" % (base, urllib.request.quote(who), urllib.request.quote(path, safe=""))) if who else base + path
        subprocess.run(["chromium", "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + ud, "--hide-scrollbars",
                        "--window-size=" + size, "--virtual-time-budget=8000", "--blink-settings=preferredColorScheme=%d" % light,
                        "--screenshot=" + os.path.join(out, name + ".png"), url], capture_output=True, timeout=180)
        print(name, os.path.exists(os.path.join(out, name + ".png")))
    srv.shutdown()
    gw.stop()
    llama.kill()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shots", "feeds"))
