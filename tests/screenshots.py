"""Dev tool: start the team box with fake gateway/model, create some demo state, take headless-chromium screenshots.
    TEAMBOX_ALLOW_DEV=1 python3 tests/screenshots.py <out_dir>
Needs chromium. Never touches a real model or the real box."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["TEAMBOX_ALLOW_DEV"] = "1"
from fake_gateway import FakeGateway  # noqa: E402
from teambox import config as C  # noqa: E402
from teambox.server import make_server  # noqa: E402
from test_teambox import free_port  # noqa: E402

A, ADMIN = "ana@team.example", "marjus@team.example"


def main(out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tb-shots-")
    sf = os.path.join(tmp, "s")
    open(sf, "w").write("sec")
    lp = free_port()
    llama = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_llama.py"), "--port", str(lp), "--tps", "60"])
    gw = FakeGateway("sec", [A, ADMIN], lp)
    port = free_port()
    cp = os.path.join(tmp, "c.json")
    json.dump({"port": port, "data_dir": os.path.join(tmp, "data"), "public_url": "https://team.xseed.ing", "admin_emails": [ADMIN], "dev_mode": True,
               "gateway": {"url": "http://127.0.0.1:%d" % gw.port, "secret_file": sf}, "jail": {"memory_mb": 512, "timeout_s": 20}}, open(cp, "w"))
    app, srv = make_server(C.load(cp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    time.sleep(1)

    def api(m, p, who, body=None):
        r = urllib.request.Request(base + p, json.dumps(body or {}).encode() if m == "POST" else None,
                                   {"X-Dev-Email": who, "X-Teambox": "1", "Content-Type": "application/json"}, method=m)
        return json.loads(urllib.request.urlopen(r, timeout=60).read())

    api("POST", "/api/profile", A, {"display_name": "Ana"})
    api("POST", "/api/tour", A, {"done": "sandboxes"})
    api("POST", "/api/tour", A, {"done": "builds"})
    b = api("POST", "/api/builds", A, {"sandbox": "my-sandbox", "task": "NORMAL: add an add() function with a unit test", "model": "builder-bot"})["build"]["id"]
    for _ in range(100):
        if api("GET", "/api/builds/" + b, A)["status"] not in ("running", "testing"):
            break
        time.sleep(0.3)
    api("POST", "/api/versions", A, {"model": "chat-llama", "name": "Short answers", "settings": {"system_prompt": "Answer in one sentence.", "temperature": 0.2}})
    c = api("POST", "/api/chats", A, {"model": "chat-llama", "version": "original"})["chat"]["id"]
    api("POST", "/api/chats/%s/send" % c, A, {"text": "Say hello to the team"})
    api("GET", "/api/me", ADMIN)
    shots = [
        ("01-tour-phone-light", A, "/#/tour", "390,3300", 1), ("02-tour-phone-dark", A, "/#/tour", "390,3300", 0),
        ("03-sandbox-phone-light", A, "/#/sandbox/ana-team-example/my-sandbox", "390,1400", 1),
        ("04-build-desktop-dark", A, "/#/build/" + b, "1280,1400", 0), ("05-models-desktop-light", A, "/#/models", "1280,900", 1),
        ("06-versions-phone-dark", A, "/#/versions/chat-llama", "390,1300", 0), ("07-byo-desktop-light", A, "/#/byo", "1280,1000", 1),
        ("08-admin-build-desktop-light", ADMIN, "/#/build/" + b, "1280,1300", 1), ("09-messages-soon-phone-light", A, "/#/m/messages", "390,1100", 1),
        ("10-chat-phone-light", A, "/#/chat/" + c, "390,900", 1), ("11-how-it-works-desktop-dark", A, "/how-it-works", "1280,2600", 0),
        ("12-home-desktop-light", A, "/", "1280,800", 1),
    ]
    for name, who, path, size, light in shots:
        ud = tempfile.mkdtemp(prefix="chr-")
        url = "%s/dev/login?email=%s&next=%s" % (base, urllib.request.quote(who), urllib.request.quote(path, safe=""))
        subprocess.run(["chromium", "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + ud, "--hide-scrollbars",
                        "--window-size=" + size, "--virtual-time-budget=6000", "--blink-settings=preferredColorScheme=%d" % light,
                        "--screenshot=" + os.path.join(out, name + ".png"), url], capture_output=True, timeout=120)
        print(name, os.path.exists(os.path.join(out, name + ".png")))
    srv.shutdown()
    gw.stop()
    llama.kill()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shots"))
