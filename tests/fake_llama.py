# Copied from D:/Atlas/app/team_tests/fake_llama.py (BESNIK team caps tests) for the team box tests - unchanged below.
"""A tiny stand-in for llama-server, for the make-room tests only: /health, /slots, /v1/chat/completions (stream or not).
It 'generates' max_tokens tokens at --tps tokens per second, so a request takes a known time. Never loads a model.
    python fake_llama.py --port 11561 [--tps 8]"""
import http.server
import json
import sys
import threading
import time

a = sys.argv[1:]
PORT = int(a[a.index("--port") + 1])
TPS = float(a[a.index("--tps") + 1]) if "--tps" in a else 8.0
SLOT = {"busy": False, "decoded": 0, "n_predict": -1}
LOCK = threading.Lock()


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *x):
        pass

    def _j(self, code, obj):
        d = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        if self.path == "/health":
            return self._j(200, {"status": "ok"})
        if self.path == "/slots":
            with LOCK:
                rem = (SLOT["n_predict"] - SLOT["decoded"]) if SLOT["busy"] else -1
                return self._j(200, [{"id": 0, "is_processing": SLOT["busy"], "params": {"n_predict": SLOT["n_predict"]},
                                      "next_token": [{"n_decoded": SLOT["decoded"], "n_remain": rem}]}])
        self._j(404, {"error": "no"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._j(404, {"error": "no"})
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        n = int(b.get("max_tokens") or 16)
        with LOCK:
            if SLOT["busy"]:
                return self._j(503, {"error": "busy"})
            SLOT.update(busy=True, decoded=0, n_predict=n)
        try:
            words = []
            if b.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
            for i in range(n):
                time.sleep(1.0 / TPS)
                with LOCK:
                    SLOT["decoded"] = i + 1
                words.append("w%d" % i)
                if b.get("stream"):
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": "w%d " % i}}]}) + "\n\n").encode())
                    self.wfile.flush()
            usage = {"prompt_tokens": 5, "completion_tokens": n}
            if b.get("stream"):
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}}], "usage": usage}) + "\n\ndata: [DONE]\n\n").encode())
            else:
                self._j(200, {"choices": [{"message": {"role": "assistant", "content": " ".join(words)}, "finish_reason": "length"}], "usage": usage})
        finally:
            with LOCK:
                SLOT.update(busy=False)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
srv.daemon_threads = True
srv.serve_forever()
