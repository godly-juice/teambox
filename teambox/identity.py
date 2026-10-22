"""Who is asking. Identity = the Cloudflare Access email header, trusted ONLY when the TCP peer is the local cloudflared
(trusted_proxy_ips, default 127.0.0.1/::1). Optional, recommended on the real box: verify the Access JWT
(Cf-Access-Jwt-Assertion, RS256) against the team domain's public keys, so a local process that forges the header gets nothing.
Dev mode (config dev_mode=true, local testing only): X-Dev-Email header or dev_email cookie."""
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


def resolve(cfg, client_ip, headers):
    """-> (email, None) or (None, reason). Never raises."""
    if cfg.get("dev_mode"):
        e = (headers.get("X-Dev-Email") or cookie(headers, "dev_email") or "").strip().lower()
        e = urllib.request.unquote(e)
        if e:
            return _allowed(cfg, e)
    if client_ip not in cfg["trusted_proxy_ips"]:
        return None, "requests are only accepted through the team box's own Cloudflare tunnel"
    email = (headers.get("Cf-Access-Authenticated-User-Email") or "").strip().lower()
    acc = cfg.get("access") or {}
    jwt = headers.get("Cf-Access-Jwt-Assertion") or cookie(headers, "CF_Authorization") or ""
    if acc.get("team_domain") or acc.get("require_jwt"):
        if not jwt:
            return None, "no Cloudflare Access token - sign in through team.xseed.ing"
        jemail, why = verify_access_jwt(jwt, acc)
        if not jemail:
            return None, why
        if email and email != jemail:
            return None, "the Access email header does not match the Access token"
        email = jemail
    if not email:
        return None, "no Cloudflare Access email - sign in through team.xseed.ing"
    return _allowed(cfg, email)


def _allowed(cfg, email):
    if not S.EMAIL_RX.match(email):
        return None, "that does not look like an email address"
    if cfg["member_emails"] and email not in cfg["member_emails"] and email not in cfg["admin_emails"]:
        return None, "%s is not on the team list" % email
    return email, None


def is_admin(cfg, email):
    return bool(email) and email.lower() in cfg["admin_emails"]
