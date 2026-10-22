"""A stand-in for BESNIK's team_gateway.py, for the team box tests only (never touches a real model).
Same contract: X-Team-Secret + X-Team-Account on every request, the five endpoints, refusals as {"error": {"message"}} or
{"ok": false, "refused": "..."}. Models:
  builder-bot   scripted tool calls for AI-build tests (script picked by a word in the task: NORMAL / TRIPWIRE / ESCAPE / SLOW)
  chat-llama    chats are FORWARDED to tests/fake_llama.py (the BESNIK fake llama-server), so the reply is a real HTTP round trip
  big-model     startable, but every start is refused for being over the account cap (to show refusal reasons)
Every request (path, account, body) is recorded in .calls for assertions."""
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def tc(name, args):
    return "I will use a tool.\n<tool_call>%s</tool_call>" % json.dumps({"name": name, "arguments": args})


ADD_PY = 'def add(a, b):\n    return a + b\n'
TEST_ADD = 'import unittest\nfrom add import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\nif __name__ == "__main__":\n    unittest.main()\n'
TRIP_TEST = 'import subprocess\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        subprocess.run(["nsenter", "-t", "1", "-m"])\n'
ESCAPE_TEST = ('import os, socket, unittest\n\n\nclass Escape(unittest.TestCase):\n'
               '    def test_network(self):\n        socket.create_connection(("1.1.1.1", 80), timeout=3)\n'
               '    def test_home(self):\n        print("HOME-LIST", os.listdir("/home") if os.path.exists("/home") else "no /home")\n'
               '        print("ROOT-LIST", sorted(os.listdir("/")))\n        print("PASSWD", open("/etc/passwd").read().splitlines()[0])\n')

SCRIPTS = {
    "NORMAL": [tc("write_file", {"path": "add.py", "content": ADD_PY}),
               tc("write_file", {"path": "test_add.py", "content": TEST_ADD}),
               tc("run_test", {"command": "python3 -m unittest -v test_add"}),
               tc("finish", {"summary": "Added add() with a test.", "tests": ["python3 -m unittest -v test_add"], "done": "done"})],
    "TRIPWIRE": [tc("write_file", {"path": "test_trip.py", "content": TRIP_TEST}),
                 tc("run_test", {"command": "python3 -m unittest -v test_trip"}),
                 tc("write_file", {"path": "test_trip.py", "content": TEST_ADD.replace("from add import add", "add = lambda a, b: a + b")}),
                 tc("run_test", {"command": "python3 -m unittest -v test_trip"}),
                 tc("finish", {"summary": "Wrote the test without the blocked word.", "tests": ["python3 -m unittest -v test_trip"], "done": "done"})],
    "ESCAPE": [tc("write_file", {"path": "test_escape.py", "content": ESCAPE_TEST}),
               tc("run_test", {"command": "python3 -m unittest -v test_escape"}),
               tc("finish", {"summary": "tried", "tests": [], "done": "failed"})],
    "GUARD": [tc("read_file", {"path": "../../../etc/passwd"}),
              tc("write_file", {"path": "CLAUDE.md", "content": "no rules"}),
              tc("write_file", {"path": ".git/config", "content": "x"}),
              tc("finish", {"summary": "guard probes", "tests": [], "done": "failed"})],
}


class FakeGateway:
    def __init__(self, secret, accounts, llama_port=None):
        self.secret, self.accounts, self.llama_port = secret, set(accounts), llama_port
        self.calls = []
        self.slow_s = 0.0
        fg = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _j(self, code, obj):
                d = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(d)))
                self.end_headers()
                self.wfile.write(d)

            def _auth(self):
                acct = self.headers.get("X-Team-Account") or ""
                if self.headers.get("X-Team-Secret") != fg.secret:
                    self._j(401, {"error": {"code": "bad_secret", "message": "missing or wrong team gateway secret"}})
                    return None
                if acct not in fg.accounts:
                    self._j(403, {"error": {"code": "unknown_account", "message": "account '%s' is not in the team policy" % acct}})
                    return None
                return acct

            def do_GET(self):
                acct = self._auth()
                if acct is None:
                    return
                fg.calls.append({"path": self.path, "account": acct})
                if self.path == "/team/models":
                    return self._j(200, {"models": [
                        {"id": "builder-bot", "name": "Builder bot (fake)", "state": "up", "startable": False, "ram_gb": 1, "limits": {"ctx_max": 16384}},
                        {"id": "chat-llama", "name": "Chat llama (fake)", "state": "up", "startable": False, "ram_gb": 1, "limits": {"ctx_max": 8192}},
                        {"id": "big-model", "name": "Big model (fake)", "state": "off", "startable": True, "ram_gb": 17.1, "limits": {}}],
                        "notices": [], "your_caps": {"ram_gb_cap": 1, "max_models": 1},
                        "team": {"models_running": 0, "ram_gb": 0, "max_models": 1, "ram_gb_cap": 12}})
                if self.path == "/v1/models":
                    return self._j(200, {"object": "list", "data": [{"id": "builder-bot"}, {"id": "chat-llama"}]})
                return self._j(404, {"error": {"code": "not_found", "message": "no"}})

            def do_POST(self):
                acct = self._auth()
                if acct is None:
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fg.calls.append({"path": self.path, "account": acct, "body": body})
                if self.path == "/team/start":
                    return self._j(409, {"ok": False, "refused": "'%s' needs ~17.1 GB RAM; your account uses 0.0 of its 1.0 GB cap" % body.get("model")})
                if self.path == "/team/stop":
                    return self._j(403, {"error": {"code": "not_team_started", "message": "'%s' was not started by the team through this gateway - only Marjus can stop it" % body.get("model")}})
                if self.path != "/v1/chat/completions":
                    return self._j(404, {"error": {"code": "not_found", "message": "no"}})
                model = body.get("model")
                if model == "chat-llama":
                    up = {"messages": body["messages"], "max_tokens": 6}
                    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % fg.llama_port, json.dumps(up).encode(),
                                                                      {"Content-Type": "application/json"}), timeout=30)
                    j = json.loads(r.read())
                    j["model"] = model
                    return self._j(200, j)
                if model != "builder-bot":
                    return self._j(403, {"error": {"code": "model_not_shared", "message": "model '%s' is not shared with the team" % model}})
                task = body["messages"][1]["content"]
                key = next((k for k in SCRIPTS if k in task), "NORMAL")
                step = sum(1 for m in body["messages"] if m["role"] == "assistant")
                if "SLOW" in task:
                    time.sleep(fg.slow_s or 2.0)
                script = SCRIPTS[key]
                content = script[min(step, len(script) - 1)]
                return self._j(200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 10}, "model": model})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
