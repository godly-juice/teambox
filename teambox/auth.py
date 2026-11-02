"""SIGN-IN without Cloudflare Access (the owner, 2026-10-21: "lumi creates them an account ... a code to put in to verify its them. they put
in the code and lumi asks them to enter a new code which will be their pin, also a password for their vault").

Accounts exist ONLY because an admin (the owner, or Lumi through the admin invite API) invited someone. There is no self sign-up.
  1. INVITE  (admin):  name, phone?, email?, purpose, language?  -> a one-time code "TTTT SSSS SSSS" (12 digits), shown once.
     The first 4 digits are the invite's tag (so wrong tries count against THAT invite), the other 8 are secret. Stored: the tag and
     scrypt(HMAC(pepper, code)) only. Expires after auth.invite_hours (48 h). Works once. invite_max_tries wrong codes -> locked.
  2. JOIN    (the invited person, first visit): enters the code -> a short-lived setup ticket (cookie tb_setup, 30 min). The code is
     used up at this moment.
  3. SETUP   chooses a sign-in name (phone / email / name) + a PIN (digits, at least 6) -> the account is active, a session starts.
     (The vault password never reaches the server - see vault.py; the profile form after it is optional.)
  4. SIGN-IN later: sign-in name + PIN -> session cookie tb_session (HttpOnly, Secure, SameSite=Lax), idle + absolute timeouts,
     "sign out everywhere".
Brute force: every secret compare is constant-time on hashes; wrong PINs lock the ACCOUNT (auth.account_max_fails -> lock_minutes,
doubling each time); wrong anything counts per IP (ip_max_fails in ip_window_minutes -> blocked ip_block_minutes); wrong invite codes
also count box-wide (global_join_fails_per_hour -> joining pauses for an hour). The IP is CF-Connecting-IP when the TCP peer is the
local cloudflared, else the peer. IP counters live in memory (a restart clears them); account and invite counters are on disk.

ADMIN API TOKENS (for Lumi on BESNIK): "tbxadm_..." bearer tokens, stored as sha256 only. Made at install time with the CLI:
    python3 -m teambox.auth new-api-token --config /etc/teambox/config.json --out /etc/teambox/invite-api.token
THE FIRST ADMIN (the owner) is invited from the CLI the same way everyone else is, with role admin:
    python3 -m teambox.auth admin-invite --config /etc/teambox/config.json --name the owner
Layout under data_dir/auth/ (directory 0700):
  pepper          32 random bytes (hex) - HMAC key for every PIN/code hash. Back it up with the data dir; without it no PIN works.
  accounts.json   {account id: {id, name, role, status, identifiers, pin{salt,hash,n}, fails, locked_until, lock_level, ...}}
  invites.json    {invite id: {id, tag, salt, hash, name, phone, email, purpose, language, role, account, status, tries, ...}}
  sessions.json   {sha256(session token): {account, created, last, ip, ua}}
  api-tokens.json {sha256(token): {id, label, created, last_used}}
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import unicodedata

from . import store as S

LOCK = threading.RLock()
DEFAULTS = {
    "invite_hours": 48, "invite_max_tries": 5, "setup_minutes": 30,
    "pin_min": 6, "pin_max": 12,
    "account_max_fails": 5, "lock_minutes": 15,
    "ip_max_fails": 20, "ip_window_minutes": 15, "ip_block_minutes": 30,
    "global_join_fails_per_hour": 200,
    "idle_minutes": 240, "absolute_hours": 72,
    "cookie_secure": True,
    "id_domain": "team.xseed.ing",          # account ids look like  ana@team.xseed.ing  (what the team gateway policy lists)
    "scrypt_n": 16384,
}
COMMON_PINS = {"123456", "654321", "111111", "000000", "121212", "112233", "123123", "123321", "696969", "159753", "147258",
               "666666", "888888", "999999", "1234567", "12345678", "123456789", "1234567890", "987654", "000001", "102030", "112211"}
STATUSES = ("pending", "redeemed", "used", "revoked", "locked", "expired")


class AuthError(Exception):
    def __init__(self, code, msg, extra=None):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra or {}


def conf(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("auth") or {}).items() if k in DEFAULTS})
    return out


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def digits(s):
    return re.sub(r"\D", "", str(s or ""))


def norm_identifier(s):
    """Sign-in name -> its stored form. Phone numbers: digits only (so '+1 (402) 555-1234' == '14025551234').
    Everything else: NFKC, lower case, single spaces."""
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if re.fullmatch(r"[+()\d .\-]{7,24}", s) and len(digits(s)) >= 7:
        return digits(s)
    return s


def check_identifier(s):
    n = norm_identifier(s)
    if len(n) < 3 or len(n) > 64:
        raise AuthError(400, "your sign-in name must be 3 to 64 characters (your phone number, email or a name)")
    if not all(ch.isalnum() or ch in " .@_+-'" for ch in n):
        raise AuthError(400, "use letters, numbers, spaces and . @ _ + - only in your sign-in name")
    return n


def check_pin(pin, c):
    pin = str(pin or "")
    if not pin.isdigit() or not pin.isascii():
        raise AuthError(400, "your PIN must be digits only")
    if not int(c["pin_min"]) <= len(pin) <= int(c["pin_max"]):
        raise AuthError(400, "your PIN must be %s to %s digits" % (c["pin_min"], c["pin_max"]))
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    if len(set(pin)) == 1 or steps in ({1}, {-1}) or pin in COMMON_PINS or len(set(pin)) <= 2:
        raise AuthError(400, "that PIN is too easy to guess - pick digits that are not all the same, not in a row and not a common PIN")
    return pin


def handle_of(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30]
    return s or "member"


class Auth:
    def __init__(self, cfg, store, log=None):
        self.cfg, self.store, self.log = cfg, store, log or (lambda m: None)
        self.dir = store.p("auth")
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.f_acc, self.f_inv = os.path.join(self.dir, "accounts.json"), os.path.join(self.dir, "invites.json")
        self.f_ses, self.f_tok = os.path.join(self.dir, "sessions.json"), os.path.join(self.dir, "api-tokens.json")
        self.pepper = self._pepper()
        self.sessions = S.jread(self.f_ses, {}) or {}
        self.ip_fails, self.ip_block, self.join_fails = {}, {}, []
        self._dummy = self.hash_secret("dummy-%s" % secrets.token_hex(8), b"0" * 16)

    @property
    def c(self):
        return conf(self.cfg)

    # ------------------------------------------------------------ secrets
    def _pepper(self):
        p = os.path.join(self.dir, "pepper")
        with LOCK:
            if not os.path.exists(p):
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_hex(32))
            with open(p, encoding="ascii") as fh:
                return bytes.fromhex(fh.read().strip())

    def hash_secret(self, secret, salt, n=None):
        n = int(n or self.c["scrypt_n"])
        pre = hmac.new(self.pepper, str(secret).encode("utf8"), hashlib.sha256).digest()
        return hashlib.scrypt(pre, salt=salt, n=n, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=32).hex()

    def make_hash(self, secret):
        salt = secrets.token_bytes(16)
        n = int(self.c["scrypt_n"])
        return {"salt": salt.hex(), "hash": self.hash_secret(secret, salt, n), "n": n}

    def verify_hash(self, secret, rec):
        if not rec:
            self.hash_secret(secret, b"0" * 16)                    # same work for "no such thing" - no timing hint
            return False
        got = self.hash_secret(secret, bytes.fromhex(rec["salt"]), rec.get("n"))
        return hmac.compare_digest(got, rec["hash"])

    # ------------------------------------------------------------ files
    def accounts(self):
        return S.jread(self.f_acc, {}) or {}

    def save_accounts(self, a):
        S.jwrite(self.f_acc, a)

    def invites(self):
        return S.jread(self.f_inv, {}) or {}

    def save_invites(self, i):
        S.jwrite(self.f_inv, i)

    def save_sessions(self):
        S.jwrite(self.f_ses, self.sessions)

    def account(self, acct_id):
        return self.accounts().get(acct_id)

    def by_identifier(self, ident):
        n = norm_identifier(ident)
        for a in self.accounts().values():
            if n in (a.get("identifiers") or []):
                return a
        return None

    # ------------------------------------------------------------ throttles
    def ip_check(self, ip):
        now = time.time()
        with LOCK:
            until = self.ip_block.get(ip, 0)
            if until > now:
                raise AuthError(429, "too many wrong tries from your network - wait %d minutes and try again" % max(1, round((until - now) / 60)))

    def ip_fail(self, ip):
        c, now = self.c, time.time()
        with LOCK:
            lst = [t for t in self.ip_fails.get(ip, []) if t > now - float(c["ip_window_minutes"]) * 60]
            lst.append(now)
            self.ip_fails[ip] = lst
            if len(lst) >= int(c["ip_max_fails"]):
                self.ip_block[ip] = now + float(c["ip_block_minutes"]) * 60
                self.ip_fails[ip] = []
                self.log("auth: %s blocked for %s min after %d wrong tries" % (ip, c["ip_block_minutes"], len(lst)))

    def join_paused(self):
        now = time.time()
        with LOCK:
            self.join_fails = [t for t in self.join_fails if t > now - 3600]
            return len(self.join_fails) >= int(self.c["global_join_fails_per_hour"])

    # ------------------------------------------------------------ invites
    def _new_account_id(self, name, accounts, invites):
        used = set(accounts) | {i.get("account") for i in invites.values() if i.get("status") in ("pending", "redeemed")}
        base = handle_of(name)
        dom = self.c["id_domain"]
        n, cand = 2, "%s@%s" % (base, dom)
        while cand in used:
            cand, n = "%s-%d@%s" % (base, n, dom), n + 1
        return cand

    def status_of(self, inv, now=None):
        now = now or time.time()
        st = inv.get("status", "pending")
        if st in ("pending", "redeemed") and now > float(inv["expires_at"]):
            return "expired"
        return st

    def create_invite(self, name, purpose, phone=None, email=None, language=None, role="member", by="admin"):
        name = str(name or "").strip()[:80]
        purpose = str(purpose or "").strip()[:600]
        if not name:
            raise AuthError(400, "who is it for? (name)")
        if not purpose:
            raise AuthError(400, "what are they here to do? (purpose) - Lumi uses it to welcome them and to name their first project")
        phone = str(phone or "").strip()[:40] or None
        if phone and len(digits(phone)) < 7:
            raise AuthError(400, "that phone number looks too short")
        email = str(email or "").strip().lower()[:200] or None
        if email and not S.EMAIL_RX.match(email):
            raise AuthError(400, "that email address does not look right")
        language = str(language or "en").strip()[:12]
        if not re.fullmatch(r"[A-Za-z-]{2,12}", language):
            language = "en"
        if role not in ("member", "admin"):
            raise AuthError(400, "role must be member or admin")
        c, now = self.c, time.time()
        with LOCK:
            invs, accs = self.invites(), self.accounts()
            live_tags = {i["tag"] for i in invs.values() if self.status_of(i, now) == "pending"}
            for _ in range(200):
                tag = "%04d" % secrets.randbelow(10000)
                if tag not in live_tags:
                    break
            else:
                raise AuthError(503, "too many open invites right now - revoke some first")
            code = tag + "%08d" % secrets.randbelow(10 ** 8)
            iid = "inv_" + secrets.token_hex(6)
            h = self.make_hash(code)
            inv = {"id": iid, "tag": tag, "salt": h["salt"], "hash": h["hash"], "n": h["n"], "name": name, "phone": phone, "email": email,
                   "purpose": purpose, "language": language, "role": role, "account": self._new_account_id(name, accs, invs),
                   "status": "pending", "tries": 0, "created": iso(now), "created_at": now,
                   "expires_at": now + float(c["invite_hours"]) * 3600, "by": by, "used_at": None}
            invs[iid] = inv
            self.save_invites(invs)
        self.log("auth: invite %s for %r (%s) created by %s, expires %s" % (iid, name, role, by, iso(inv["expires_at"])))
        return inv, "%s %s %s" % (code[:4], code[4:8], code[8:])

    def invite_view(self, inv):
        return {"id": inv["id"], "name": inv["name"], "phone": inv.get("phone"), "email": inv.get("email"), "purpose": inv["purpose"],
                "language": inv.get("language"), "role": inv.get("role", "member"), "account": inv.get("account"),
                "status": self.status_of(inv), "created": inv.get("created"), "expires": iso(inv["expires_at"]),
                "expires_at": int(inv["expires_at"]), "used_at": inv.get("used_at"), "by": inv.get("by")}

    def list_invites(self):
        return sorted((self.invite_view(i) for i in self.invites().values()), key=lambda x: x["created"] or "", reverse=True)

    def revoke_invite(self, iid, by):
        with LOCK:
            invs = self.invites()
            inv = invs.get(str(iid or ""))
            if not inv:
                raise AuthError(404, "no such invite")
            if inv.get("status") == "used":
                raise AuthError(409, "that invite was already used - the account exists (disable it instead)")
            inv["status"] = "revoked"
            inv["revoked_by"], inv["revoked_at"] = by, iso(time.time())
            inv.pop("setup", None)
            self.save_invites(invs)
        self.log("auth: invite %s revoked by %s" % (iid, by))
        return self.invite_view(inv)

    def redeem(self, code, ip):
        """The person types the code. -> (invite view, setup ticket). The code is used up on success."""
        self.ip_check(ip)
        bad = AuthError(400, "that code is not right, or it has expired or was already used. Codes have 12 digits, work once and "
                             "expire after %s hours - ask the owner (or Lumi) for a new one if needed." % self.c["invite_hours"])
        if self.join_paused():
            raise AuthError(429, "joining is paused for a while because of many wrong codes - try again in an hour")
        d = digits(code)
        now = time.time()
        with LOCK:
            invs = self.invites()
            if len(d) != 12:
                self.ip_fail(ip)
                raise bad
            cands = [i for i in invs.values() if i["tag"] == d[:4] and self.status_of(i, now) == "pending"]
            inv = cands[0] if cands else None
            ok = self.verify_hash(d, {"salt": inv["salt"], "hash": inv["hash"], "n": inv.get("n")} if inv else None)
            if not ok:
                self.ip_fail(ip)
                self.join_fails.append(now)
                if inv:
                    inv["tries"] = int(inv.get("tries") or 0) + 1
                    if inv["tries"] >= int(self.c["invite_max_tries"]):
                        inv["status"] = "locked"
                        self.log("auth: invite %s LOCKED after %d wrong codes (last from %s)" % (inv["id"], inv["tries"], ip))
                    self.save_invites(invs)
                raise bad
            ticket = secrets.token_urlsafe(32)
            inv["status"] = "redeemed"
            inv["redeemed_at"] = iso(now)
            inv["setup"] = {"hash": hashlib.sha256(ticket.encode()).hexdigest(), "until": now + float(self.c["setup_minutes"]) * 60}
            self.save_invites(invs)
        self.log("auth: invite %s redeemed from %s" % (inv["id"], ip))
        return self.invite_view(inv), ticket

    def setup_invite(self, ticket):
        """-> the invite this setup ticket belongs to, or None."""
        if not ticket or len(ticket) > 100:
            return None
        h = hashlib.sha256(ticket.encode()).hexdigest()
        now = time.time()
        for inv in self.invites().values():
            st = inv.get("setup") or {}
            if st and hmac.compare_digest(st.get("hash", ""), h) and st.get("until", 0) > now and self.status_of(inv, now) == "redeemed":
                return inv
        return None

    def complete_setup(self, ticket, identifier, pin, ip, ua=""):
        """Sign-in name + PIN -> (account, session token)."""
        self.ip_check(ip)
        c = self.c
        with LOCK:
            inv = self.setup_invite(ticket)
            if not inv:
                raise AuthError(401, "your setup step has expired - ask the owner (or Lumi) for a new invite code")
            ident = check_identifier(identifier)
            pin = check_pin(pin, c)
            accs = self.accounts()
            if any(ident in (a.get("identifiers") or []) for a in accs.values()):
                raise AuthError(409, "that sign-in name is taken - pick another (for example your phone number or email)")
            for ph in (digits(inv.get("phone")), ident if ident.isdigit() else ""):
                if ph and pin in ph:
                    raise AuthError(400, "do not use (part of) your phone number as your PIN")
            aid = inv["account"]
            if aid in accs:
                raise AuthError(409, "this invite's account already exists")
            accs[aid] = {"id": aid, "name": inv["name"], "role": inv.get("role", "member"), "status": "active", "identifiers": [ident],
                         "pin": self.make_hash(pin), "created": iso(time.time()), "invite": inv["id"], "purpose": inv["purpose"],
                         "language": inv.get("language"), "phone": inv.get("phone"), "email": inv.get("email"),
                         "fails": 0, "locked_until": 0, "lock_level": 0}
            self.save_accounts(accs)
            invs = self.invites()
            i2 = invs[inv["id"]]
            i2["status"], i2["used_at"] = "used", iso(time.time())
            i2.pop("setup", None)
            self.save_invites(invs)
        self.log("auth: account %s created from invite %s (sign-in name set) from %s" % (aid, inv["id"], ip))
        return accs[aid], self.new_session(aid, ip, ua)

    # ------------------------------------------------------------ sign-in
    def login(self, identifier, pin, ip, ua=""):
        self.ip_check(ip)
        c, now = self.c, time.time()
        bad = AuthError(401, "that sign-in name and PIN do not match")
        with LOCK:
            accs = self.accounts()
            n = norm_identifier(identifier)
            a = next((x for x in accs.values() if n and n in (x.get("identifiers") or [])), None)
            if a and float(a.get("locked_until") or 0) > now:
                self.hash_secret("x", b"0" * 16)
                raise AuthError(429, "this account is locked for %d more minutes after too many wrong PINs (an admin can unlock it)"
                                % max(1, round((a["locked_until"] - now) / 60)))
            ok = self.verify_hash(str(pin or ""), a.get("pin") if a else None)
            if not ok or not a:
                self.ip_fail(ip)
                if a:
                    a["fails"] = int(a.get("fails") or 0) + 1
                    if a["fails"] >= int(c["account_max_fails"]):
                        lvl = int(a.get("lock_level") or 0)
                        a["locked_until"] = now + float(c["lock_minutes"]) * 60 * (2 ** min(lvl, 6))
                        a["lock_level"], a["fails"] = lvl + 1, 0
                        self.log("auth: account %s LOCKED until %s (wrong PINs, last from %s)" % (a["id"], iso(a["locked_until"]), ip))
                    self.save_accounts(accs)
                raise bad
            if a.get("status") != "active":
                raise AuthError(403, "this account is switched off - ask the owner")
            a["fails"], a["lock_level"], a["last_login"] = 0, 0, iso(now)
            self.save_accounts(accs)
        self.log("auth: %s signed in from %s" % (a["id"], ip))
        return a, self.new_session(a["id"], ip, ua)

    def change_pin(self, acct_id, old, new, ip):
        self.ip_check(ip)
        with LOCK:
            accs = self.accounts()
            a = accs.get(acct_id)
            if not a or not self.verify_hash(str(old or ""), a.get("pin")):
                self.ip_fail(ip)
                raise AuthError(403, "your current PIN is not right")
            new = check_pin(new, self.c)
            a["pin"] = self.make_hash(new)
            self.save_accounts(accs)
        self.log("auth: %s changed their PIN" % acct_id)

    def check_pin_of(self, acct_id, pin, ip):
        self.ip_check(ip)
        a = self.account(acct_id)
        if not a or not self.verify_hash(str(pin or ""), a.get("pin")):
            self.ip_fail(ip)
            raise AuthError(403, "your PIN is not right")

    def unlock(self, ident):
        with LOCK:
            accs = self.accounts()
            n = norm_identifier(ident)
            a = accs.get(ident) or next((x for x in accs.values() if n in (x.get("identifiers") or [])), None)
            if not a:
                raise AuthError(404, "no such account")
            a["locked_until"], a["fails"], a["lock_level"] = 0, 0, 0
            self.save_accounts(accs)
            return a["id"]

    def set_status(self, acct_id, status, by):
        with LOCK:
            accs = self.accounts()
            a = accs.get(acct_id)
            if not a:
                raise AuthError(404, "no such account")
            a["status"] = status
            self.save_accounts(accs)
        if status != "active":
            self.logout_all(acct_id)
        self.log("auth: account %s set to %s by %s" % (acct_id, status, by))

    def update_account(self, acct_id, **kw):
        with LOCK:
            accs = self.accounts()
            if acct_id in accs:
                accs[acct_id].update(kw)
                self.save_accounts(accs)

    # ------------------------------------------------------------ sessions
    def new_session(self, acct_id, ip, ua=""):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        with LOCK:
            self.sessions[hashlib.sha256(tok.encode()).hexdigest()] = {"account": acct_id, "created": now, "last": now, "ip": ip,
                                                                         "ua": str(ua or "")[:120]}
            self._prune()
            self.save_sessions()
        return tok

    def _prune(self):
        c, now = self.c, time.time()
        dead = [k for k, s in self.sessions.items() if now - s["last"] > float(c["idle_minutes"]) * 60
                or now - s["created"] > float(c["absolute_hours"]) * 3600]
        for k in dead:
            self.sessions.pop(k, None)
        return bool(dead)

    def session(self, tok):
        """-> (account, None) or (None, why)."""
        if not tok or len(tok) > 100:
            return None, "not signed in"
        c, now = self.c, time.time()
        h = hashlib.sha256(tok.encode()).hexdigest()
        with LOCK:
            s = self.sessions.get(h)
            if not s:
                return None, "not signed in (or signed out)"
            if now - s["created"] > float(c["absolute_hours"]) * 3600:
                self.sessions.pop(h, None)
                self.save_sessions()
                return None, "you were signed out after %s hours - sign in again" % c["absolute_hours"]
            if now - s["last"] > float(c["idle_minutes"]) * 60:
                self.sessions.pop(h, None)
                self.save_sessions()
                return None, "you were signed out after %s minutes without activity - sign in again" % c["idle_minutes"]
            a = self.account(s["account"])
            if not a or a.get("status") != "active":
                self.sessions.pop(h, None)
                self.save_sessions()
                return None, "this account is switched off"
            if now - s["last"] > 60:
                s["last"] = now
                self.save_sessions()
            return a, None

    def logout(self, tok):
        with LOCK:
            if self.sessions.pop(hashlib.sha256(str(tok or "").encode()).hexdigest(), None):
                self.save_sessions()

    def logout_all(self, acct_id):
        with LOCK:
            n = len(self.sessions)
            self.sessions = {k: v for k, v in self.sessions.items() if v["account"] != acct_id}
            self.save_sessions()
            return n - len(self.sessions)

    def sessions_of(self, acct_id):
        return [{"created": iso(s["created"]), "last": iso(s["last"]), "ip": s.get("ip"), "ua": s.get("ua")}
                for s in self.sessions.values() if s["account"] == acct_id]

    def cookie(self, name, value, max_age, same_site="Lax"):
        sec = "; Secure" if self.c["cookie_secure"] else ""
        return "%s=%s; Path=/; HttpOnly%s; SameSite=%s; Max-Age=%d" % (name, value, sec, same_site, int(max_age))

    # ------------------------------------------------------------ admin API tokens (Lumi's invite tool on BESNIK)
    def new_api_token(self, label):
        tok = "tbxadm_" + secrets.token_urlsafe(32)
        with LOCK:
            t = S.jread(self.f_tok, {}) or {}
            t[hashlib.sha256(tok.encode()).hexdigest()] = {"id": secrets.token_hex(4), "label": str(label or "api")[:60],
                                                             "created": iso(time.time()), "last_used": None}
            S.jwrite(self.f_tok, t)
        return tok

    def api_tokens(self):
        return [dict(v, sha256_prefix=k[:12]) for k, v in (S.jread(self.f_tok, {}) or {}).items()]

    def revoke_api_token(self, tid):
        with LOCK:
            t = S.jread(self.f_tok, {}) or {}
            keep = {k: v for k, v in t.items() if v["id"] != tid}
            S.jwrite(self.f_tok, keep)
            return len(keep) != len(t)

    def check_api_token(self, tok, ip):
        self.ip_check(ip)
        if not tok or not tok.startswith("tbxadm_") or len(tok) > 200:
            self.ip_fail(ip)
            return None
        h = hashlib.sha256(tok.encode()).hexdigest()
        with LOCK:
            t = S.jread(self.f_tok, {}) or {}
            hit = next((k for k in t if hmac.compare_digest(k, h)), None)
            if not hit:
                self.ip_fail(ip)
                return None
            t[hit]["last_used"] = iso(time.time())
            S.jwrite(self.f_tok, t)
            return t[hit]


# ====================================================================== CLI (install time, on the team box)
def main(argv=None):
    import argparse
    from . import config as C
    ap = argparse.ArgumentParser(prog="python3 -m teambox.auth")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("admin-invite", help="invite an ADMIN (the owner) - prints the one-time code ONCE")
    a1.add_argument("--name", required=True)
    a1.add_argument("--purpose", default="run the team box (admin)")
    a1.add_argument("--email")
    a1.add_argument("--phone")
    a2 = sub.add_parser("new-api-token", help="make the admin API token for Lumi's invite tool; written to --out (0600), never printed")
    a2.add_argument("--out", required=True)
    a2.add_argument("--label", default="lumi-invite (BESNIK)")
    sub.add_parser("list", help="accounts + invites (no secrets)")
    a4 = sub.add_parser("unlock", help="clear a PIN lockout")
    a4.add_argument("who")
    a5 = sub.add_parser("signout-all", help="end every session of an account")
    a5.add_argument("who")
    args = ap.parse_args(argv)
    cfg = C.load(args.config)
    st = S.Store(cfg["data_dir"])
    au = Auth(cfg, st, lambda m: open(st.p("teambox.log"), "a", encoding="utf8").write("%s %s\n" % (S.now_s(), m)))
    if args.cmd == "admin-invite":
        inv, code = au.create_invite(args.name, args.purpose, args.phone, args.email, role="admin", by="install-cli")
        print("ADMIN invite for %s (account %s)\n  open:    %s/join\n  code:    %s   (shown only now; works once; expires %s)"
              % (inv["name"], inv["account"], cfg["public_url"].rstrip("/"), code, iso(inv["expires_at"])))
    elif args.cmd == "new-api-token":
        tok = au.new_api_token(args.label)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(tok + "\n")
        print("admin API token written to %s (0600; sha256 %s...). Copy that file WHOLE to BESNIK, then delete it here."
              % (args.out, hashlib.sha256(tok.encode()).hexdigest()[:12]))
    elif args.cmd == "list":
        print(json.dumps({"accounts": [{k: a.get(k) for k in ("id", "name", "role", "status", "identifiers", "created", "locked_until")}
                                       for a in au.accounts().values()], "invites": au.list_invites(),
                          "api_tokens": au.api_tokens()}, indent=1))
    elif args.cmd == "unlock":
        print("unlocked", au.unlock(args.who))
    elif args.cmd == "signout-all":
        a = au.by_identifier(args.who) or au.account(args.who)
        if not a:
            sys.exit("no such account")
        print("ended %d session(s) of %s" % (au.logout_all(a["id"]), a["id"]))


if __name__ == "__main__":
    main()
