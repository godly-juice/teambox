"""Who is asking. Since 2026-10-21 (Marjus dropped the Cloudflare Access email check) identity = the team box's OWN sign-in session
(auth.py: invite code -> PIN). Requests are still accepted only from the local cloudflared (trusted_proxy_ips, default 127.0.0.1/::1).
Cloudflare Access can be switched back on as an extra OUTER lock (config access.enabled; the RS256 Access JWT is verified against the
team domain's public keys when access.team_domain is set) - it never decides who you are.
Dev mode (config dev_mode=true + env TEAMBOX_ALLOW_DEV=1, local testing only): X-Dev-Email header or dev_email cookie."""
import base64
import hashlib
import hmac
import json
import time
import urllib.request

from . import store as S

_CERTS = {"t": 0, "keys": {}}


def b64url(s):
    s = s.encode() if isinstance(s, str) else s
    return base64.urlsafe_b64decode(s + b"=" * (-len(s) % 4))


DIGESTINFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verify_sha256(n, e, msg, sig):
    """RSASSA-PKCS1-v1_5 with SHA-256 (what RS256 means), standard library only."""
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    s = int.from_bytes(sig, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = DIGESTINFO_SHA256 + hashlib.sha256(msg).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _keys(acc):
    if time.time() - _CERTS["t"] < 3600 and _CERTS["keys"]:
        return _CERTS["keys"]
    if acc.get("certs_file"):
        with open(acc["certs_file"], encoding="utf8") as fh:
            j = json.load(fh)
    else:
        url = "https://%s/cdn-cgi/access/certs" % acc["team_domain"]
        with urllib.request.urlopen(url, timeout=10) as r:
            j = json.loads(r.read())
    keys = {}
    for k in j.get("keys") or []:
        if k.get("kty") == "RSA" and k.get("kid"):
            keys[k["kid"]] = (int.from_bytes(b64url(k["n"]), "big"), int.from_bytes(b64url(k["e"]), "big"))
    _CERTS.update(t=time.time(), keys=keys)
    return keys


def verify_access_jwt(token, acc, now=None):
    """-> (email, None) or (None, why)."""
    try:
        h64, p64, s64 = token.split(".")
        head, claims, sig = json.loads(b64url(h64)), json.loads(b64url(p64)), b64url(s64)
    except Exception:  # noqa: BLE001
        return None, "the Access token is malformed"
    if head.get("alg") != "RS256":
        return None, "the Access token is not RS256"
    try:
        keys = _keys(acc)
    except Exception as exc:  # noqa: BLE001
        return None, "could not load the Access public keys (%s)" % type(exc).__name__
    key = keys.get(head.get("kid"))
    if not key:
        _CERTS["t"] = 0                                   # keys may have rotated: refetch next time
        return None, "the Access token's key is unknown"
    if not rsa_verify_sha256(key[0], key[1], (h64 + "." + p64).encode(), sig):
        return None, "the Access token's signature is wrong"
    now = now or time.time()
    if float(claims.get("exp") or 0) < now - 30:
        return None, "the Access token has expired"
    if float(claims.get("nbf") or 0) > now + 30:
        return None, "the Access token is not valid yet"
    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]
    if acc.get("aud") and acc["aud"] not in aud:
        return None, "the Access token is for another application"
    if acc.get("team_domain") and claims.get("iss") not in ("https://" + acc["team_domain"], acc["team_domain"]):
        return None, "the Access token comes from another Access team"
    email = (claims.get("email") or "").strip().lower()
    if not email:
        return None, "the Access token has no email"
    return email, None


def cookie(headers, name):
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def client_ip(cfg, peer, headers):
    """The address used for brute-force limits: Cloudflare's CF-Connecting-IP when the TCP peer is the local cloudflared, else the peer."""
    if peer in cfg["trusted_proxy_ips"]:
        cf = (headers.get("CF-Connecting-IP") or "").strip()
        if cf and len(cf) <= 64 and all(ch in "0123456789abcdefABCDEF.:" for ch in cf):
            return cf
    return peer


def access_lock(cfg, peer, headers):
    """OPTIONAL OUTER LOCK (config access.enabled, off by default since 2026-10-21): when on, a request must ALSO carry a valid
    Cloudflare Access login (the RS256 token is verified when access.team_domain is set). It never decides WHO you are - that is
    always the app's own sign-in. -> None when allowed, else the reason."""
    acc = cfg.get("access") or {}
    if not acc.get("enabled"):
        return None
    if peer not in cfg["trusted_proxy_ips"]:
        return "requests are only accepted through the team box's own Cloudflare tunnel"
    jwt = headers.get("Cf-Access-Jwt-Assertion") or cookie(headers, "CF_Authorization") or ""
    if acc.get("team_domain") or acc.get("require_jwt"):
        if not jwt:
            return "no Cloudflare Access token - open the site through team.xseed.ing"
        jemail, why = verify_access_jwt(jwt, acc)
        return None if jemail else why
    if not (headers.get("Cf-Access-Authenticated-User-Email") or "").strip():
        return "no Cloudflare Access login - open the site through team.xseed.ing"
    return None


def resolve(cfg, auth, peer, headers):
    """WHO is asking -> ({"email": account id, "account": account or None, "via": "session"|"dev"}, None) or (None, reason).
    Identity = the team box's own sign-in session (cookie tb_session). The Cloudflare Access email header is NOT an identity any more.
    Dev mode (tests/screenshots only): X-Dev-Email header or dev_email cookie. Never raises."""
    if cfg.get("dev_mode"):
        e = urllib.request.unquote((headers.get("X-Dev-Email") or cookie(headers, "dev_email") or "").strip().lower())
        if e:
            if not S.EMAIL_RX.match(e):
                return None, "that does not look like an account id"
            return {"email": e, "account": auth.account(e), "via": "dev"}, None
    if peer not in cfg["trusted_proxy_ips"]:
        return None, "requests are only accepted through the team box's own Cloudflare tunnel"
    a, why = auth.session(cookie(headers, "tb_session"))
    if not a:
        return None, why
    return {"email": a["id"], "account": a, "via": "session"}, None


def is_admin(cfg, email, account=None):
    """Admins: accounts with role admin (made with  python3 -m teambox.auth admin-invite), plus any id listed in admin_emails."""
    return bool(email) and (email.lower() in cfg["admin_emails"] or bool(account and account.get("role") == "admin"))
