"""Client for BESNIK's team gateway (team_gateway.py). Every call carries the gateway secret (read from a file on this box, never
logged) and X-Team-Account = the member's email, so BESNIK applies THAT member's caps. The gateway enforces all caps; this side only
passes its answers on - refusals are shown to the member in plain words."""
import json
import urllib.error
import urllib.request


class Gateway:
    def __init__(self, gcfg):
        self.cfg = gcfg

    def _secret(self):
        p = self.cfg.get("secret_file") or ""
        if not p:
            return ""
        try:
            with open(p, encoding="utf8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def call(self, method, path, account, body=None, timeout=None):
        """-> (status, json-or-None, plain message for the member)."""
        url = self.cfg["url"].rstrip("/") + path
        h = {"Content-Type": "application/json", "X-Team-Secret": self._secret(), "X-Team-Account": account, "User-Agent": "teambox"}
        data = json.dumps(body).encode("utf8") if body is not None else None
        req = urllib.request.Request(url, data, h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.cfg.get("timeout_s", 600)) as r:
                raw = r.read()
                try:
                    j = json.loads(raw or b"{}")
                except ValueError:
                    j = None
                return r.status, j, plain(j) if r.status >= 300 else ""
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                j = json.loads(raw or b"{}")
            except ValueError:
                j = None
            return e.code, j, plain(j) or "the team gateway answered HTTP %s" % e.code
        except OSError as e:
            return 0, None, "the team gateway on BESNIK could not be reached (%s) - BESNIK may be off or the LAN link is down" % type(e).__name__


def plain(j):
    """The gateway's own reason, word for word."""
    if not isinstance(j, dict):
        return ""
    if j.get("refused"):
        return str(j["refused"])
    err = j.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    if isinstance(err, str):
        return err
    return ""
