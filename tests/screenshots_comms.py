"""Dev tool: screenshots of Messages & calls with two real headless-Chromium browsers (fake camera/mic) and a TEST Lumi key.
    TEAMBOX_ALLOW_DEV=1 python3 tests/screenshots_comms.py <out_dir>
Needs chromium + node (tests/cdp.mjs drives the browsers). Never touches the real box or a real key."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["TEAMBOX_ALLOW_DEV"] = "1"
import comms_crypto as K  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox.server import make_server  # noqa: E402
from test_comms import free_port  # noqa: E402

ANA, BEN, CARA, ADMIN = "ana@team.example", "ben@team.example", "cara@team.example", "marjus@team.example"
M = "/api/m/messages"


def png(w, h):
    raw = b"".join(b"\x00" + b"".join(struct.pack("BBB", int(255 * x / w), int(120 + 100 * y / h), 210 - int(150 * x / w)) for x in range(w))
                   for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def main(out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tb-comms-shots-")
    lumi = K.Key()
    port = free_port()
    cp = os.path.join(tmp, "c.json")
    with open(cp, "w") as fh:
        json.dump({"port": port, "data_dir": os.path.join(tmp, "data"), "public_url": "https://team.xseed.ing", "admin_emails": [ADMIN],
                   "dev_mode": True, "gateway": {"url": "http://127.0.0.1:9"},
                   "comms": {"lumi_public_key": lumi.spki, "lumi_kid": lumi.kid, "export_token_sha256": "0" * 64}}, fh)
    app, srv = make_server(C.load(cp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    time.sleep(0.5)

    def api(m, p, who, body=None):
        r = urllib.request.Request(base + p, json.dumps(body or {}).encode() if m == "POST" else None,
                                   {"X-Dev-Email": who, "X-Teambox": "1", "Content-Type": "application/json"}, method=m)
        return json.loads(urllib.request.urlopen(r, timeout=60).read())

    for who, name in ((ANA, "Ana"), (BEN, "Ben"), (CARA, "Cara"), (ADMIN, "Marjus")):
        api("GET", "/api/me", who)
        api("POST", "/api/profile", who, {"display_name": name})
    ver = api("GET", M + "/state", BEN)["version"]
    for who in (BEN, CARA, ADMIN):
        api("POST", M + "/notice/accept", who, {"agree": True, "version": ver})
    cara_key = K.Key()
    api("POST", M + "/keys", CARA, {"spki": cara_key.spki, "label": "Cara's laptop"})
    slug = {w: api("GET", "/api/me", w)["id"] for w in (ANA, BEN, CARA, ADMIN)}

    procs = []

    def browser(who, path, dbg):
        ud = tempfile.mkdtemp(prefix="chr-", dir=tmp)
        url = "%s/dev/login?email=%s&next=%s" % (base, urllib.parse.quote(who), urllib.parse.quote(path, safe=""))
        p = subprocess.Popen(["chromium", "--headless=new", "--no-first-run", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=" + ud,
                              "--remote-debugging-port=%d" % dbg, "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                              "--autoplay-policy=no-user-gesture-required", "--disable-features=WebRtcHideLocalIpsWithMdns", "--window-size=1280,900", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return dbg

    def cdp(dbg, steps):
        f = os.path.join(tmp, "steps-%d-%d.json" % (dbg, time.time() * 1000))
        with open(f, "w") as fh:
            json.dump(steps, fh)
        r = subprocess.run(["node", os.path.join(HERE, "cdp.mjs"), str(dbg), f], capture_output=True, text=True, timeout=300)
        print((r.stdout + r.stderr).strip())

    S = lambda n: os.path.join(out, n + ".png")  # noqa: E731
    try:
        a = browser(ANA, "/#/m/messages", free_port())
        cdp(a, [{"size": [390, 900]}, {"dark": False}, {"waitFor": "#notice-agree"}, {"wait": 500}, {"shot": S("01-notice-gate-phone-light"), "full": True},
                {"size": [1280, 900]}, {"dark": True}, {"wait": 300}, {"shot": S("02-notice-gate-desktop-dark"), "full": True},
                {"dark": False}, {"click": "#notice-agree"}, {"waitFor": "#thread-new", "timeout": 300}])
        b = browser(BEN, "/#/m/messages", free_port())
        cdp(b, [{"waitFor": "#thread-new", "timeout": 300}])
        for _ in range(100):
            if app.comms.keys_of(slug[ANA]) and app.comms.keys_of(slug[BEN]):
                break
            time.sleep(0.2)
        # conversations, written by Python "as" each member, encrypted for every browser key + Lumi (same scheme as comms.js)
        t1 = api("POST", M + "/threads", ANA, {"members": [slug[BEN]]})["thread"]["id"]
        t2 = api("POST", M + "/threads", CARA, {"members": [slug[ANA], slug[BEN]]})["thread"]["id"]

        def recips(tid, who):
            th = api("GET", M + "/thread?id=" + tid, who)
            return [("lumi", th["lumi"]["kid"], th["lumi"]["spki"])] + [(m, k["kid"], k["spki"]) for m, ks in th["keys"].items() for k in ks]

        def say(who, tid, text, att=None, ck=None):
            env, ck = K.seal({"t": text, "att": att or []}, recips(tid, who), ck)
            api("POST", M + "/send", who, {"thread": tid, "env": env, "att": [x["blob"] for x in att or []]})

        say(BEN, t1, "Hi Ana! Did the parser tests pass in the jail?")
        say(ANA, t1, "Yes - all green. I'll post it in Tuesday's feed so everyone can vote on it.")
        ck = K.AESGCM.generate_key(256)
        img = png(320, 180)
        bid = api("POST", M + "/blob/start", BEN, {"kind": "att", "thread": t1})["blob"]["id"]
        iv, data = K.chunk(ck, img)
        api("POST", M + "/blob/chunk", BEN, {"id": bid, "i": 0, "iv": iv, "data": data})
        api("POST", M + "/blob/finish", BEN, {"id": bid})
        say(BEN, t1, "Here is my sketch of the new screen.", [{"blob": bid, "name": "sketch.png", "type": "image/png", "size": len(img)}], ck)
        say(CARA, t2, "Group call about the demo at 15:00?")
        say(BEN, t2, "Works for me.")

        cdp(a, [{"goto": base + "/#/m/messages/t/" + t1}, {"size": [390, 900]}, {"waitFor": ".msg-text"}, {"wait": 1500},
                {"click": ".att button"}, {"waitFor": ".att-img"}, {"wait": 500}, {"shot": S("03-thread-phone-light"), "full": True},
                {"size": [1280, 900]}, {"dark": True}, {"wait": 300}, {"shot": S("04-thread-desktop-dark")}, {"dark": False},
                {"click": "#call-video"}, {"waitFor": "#callbox"}, {"wait": 1500}])
        cdp(b, [{"waitFor": ".ring button", "timeout": 150}, {"wait": 300}, {"size": [1280, 900]}, {"shot": S("05-incoming-call-ring")}, {"click": ".ring button"},
                {"waitFor": "#callbox"}])
        time.sleep(8)
        cdp(a, [{"waitFor": "figcaption"}, {"wait": 3000}, {"shot": S("06-video-call-recording-for-lumi-desktop")},
                {"eval": "document.getElementById('callbox').innerText"}])
        cdp(b, [{"size": [390, 900]}, {"dark": True}, {"wait": 2500}, {"shot": S("07-video-call-callee-phone-dark")}])
        cdp(a, [{"eval": "(function(){var b=[].slice.call(document.querySelectorAll('#callbox button')).filter(function(x){return /End for everyone/.test(x.textContent)})[0]; if(b) b.click(); return !!b;})()"},
                {"wait": 4000}, {"goto": base + "/#/m/messages"}, {"waitFor": "#thread-new"}, {"wait": 800}, {"shot": S("08-messages-home-desktop-light"), "full": True},
                {"goto": base + "/#/m/messages/keys"}, {"waitFor": "#rec-pass1"}, {"wait": 500}, {"size": [390, 900]}, {"shot": S("09-keys-recovery-phone-light"), "full": True},
                {"size": [1280, 900]}, {"goto": base + "/how-it-works#messages"}, {"wait": 1200}, {"eval": "document.getElementById('messages').scrollIntoView()"},
                {"wait": 300}, {"shot": S("10-how-it-works-messages-desktop")}])
        c = browser(ADMIN, "/#/m/messages", free_port())
        cdp(c, [{"size": [1280, 900]}, {"waitFor": "#thread-new", "timeout": 300}, {"wait": 1500}, {"shot": S("11-admin-metadata-desktop-light"), "full": True}])
        cdp(a, [{"goto": base + "/#/tour"}, {"waitFor": "#cp-messages"}, {"wait": 500}, {"eval": "document.getElementById('cp-messages').scrollIntoView()"},
                {"size": [390, 900]}, {"wait": 300}, {"shot": S("12-tour-messages-checkpoint-phone")}])
        cl = app.comms.all_calls()
        print("calls:", json.dumps([{k: x.get(k) for k in ("started", "ended", "joined", "recording", "seconds")} for x in cl]))
    finally:
        for p in procs:
            p.kill()
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shots", "comms"))
