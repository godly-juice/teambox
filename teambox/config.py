"""Team box configuration: one JSON file (config.json next to run.py, or --config / TEAMBOX_CONFIG).
Every key has a safe default; a broken file stops the app (fail closed) instead of falling back to "open"."""
import copy
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "bind": "127.0.0.1",                     # cloudflared on the same box connects here; never 0.0.0.0
    "port": 8740,
    "data_dir": "data",                      # relative to the app folder, or absolute (/var/lib/teambox on xOS)
    "public_url": "https://team.xseed.ing",
    "admin_emails": [],                      # optional extra admin account ids (normally: the admin account made by `teambox.auth admin-invite`)
    "dev_mode": False,                       # local testing only: identity from X-Dev-Email header / dev_email cookie
    "trusted_proxy_ips": ["127.0.0.1", "::1"],
    "auth": {},                              # sign-in limits - defaults in teambox/auth.py DEFAULTS (invite_hours 48, idle_minutes 240, ...)
    "access": {                              # OPTIONAL extra OUTER lock (off): Cloudflare Access in front of the app's own sign-in
        "enabled": False,                    # true = every page/API request must also carry a Cloudflare Access login
        "team_domain": "",                   # e.g. xseed.cloudflareaccess.com  -> the Access JWT is verified when set
        "aud": "",                           # the Access application's AUD tag
        "require_jwt": False,                # true = no valid Cf-Access-Jwt-Assertion, no entry
        "certs_file": "",                    # tests only: a local JWKS file instead of fetching the certs
    },
    "welcome": {"model": "", "template": "config/templates/lumi-welcome.txt", "fallback": "config/templates/lumi-welcome-fallback.txt"},
    "gateway": {
        "url": "http://127.0.0.1:11540",     # BESNIK's team gateway (LAN address on the real box)
        "secret_file": "",                   # path to the gateway secret on THIS box (never in the repo)
        "timeout_s": 600,
    },
    "builds": {"max_total": 2, "max_steps": 40, "max_minutes": 60, "max_tokens": 2500, "default_model": ""},
    "jail": {
        "mode": "bwrap",                     # the only mode; tests/builds refuse to run without it
        "bwrap": "/usr/bin/bwrap",
        "prlimit": "/usr/bin/prlimit",
        "systemd_run": "/usr/bin/systemd-run",
        "systemd_scope": False,              # true on the real box: also a cgroup (MemoryMax/TasksMax) via systemd-run --user --scope
        "memory_mb": 1024,
        "timeout_s": 120,
        "max_procs": 256,
        "max_file_mb": 50,
        "output_max_kb": 64,
    },
    "limits": {
        "sandboxes_per_member": 10,
        "sandbox_max_mb": 200,
        "git_max_push_mb": 50,
        "chat_messages_max": 200,
        "tokens_per_member": 5,
    },
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(Exception):
    pass


def load(path=None):
    path = path or os.environ.get("TEAMBOX_CONFIG") or os.path.join(HERE, "config.json")
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf8") as fh:
                raw = json.load(fh)
        except ValueError as exc:
            raise ConfigError("config %s is not valid JSON: %s" % (path, exc))
    cfg = _merge(DEFAULTS, raw)
    if cfg["bind"] in ("0.0.0.0", "::", ""):
        raise ConfigError("bind must be a loopback address (cloudflared runs on this box) - 0.0.0.0 is refused")
    dd = cfg["data_dir"]
    cfg["data_dir"] = dd if os.path.isabs(dd) else os.path.join(HERE, dd)
    cfg["admin_emails"] = [e.strip().lower() for e in cfg["admin_emails"] if e.strip()]
    if cfg["dev_mode"] and os.environ.get("TEAMBOX_ALLOW_DEV") != "1":
        raise ConfigError("dev_mode is on in %s but TEAMBOX_ALLOW_DEV=1 is not set - refusing to start (dev mode lets anyone on this "
                          "machine pick an identity; never use it on the real team box)" % path)
    cfg["_path"] = path
    return cfg
