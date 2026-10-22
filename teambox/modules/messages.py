"""MESSAGES, CALLS and VIDEO CALLS (Marjus, 2026-10-21, team-calls-messages-monitoring-notice.txt). LIVE.
The storage + checks live in teambox/comms.py (read its docstring for exactly what the server can and can not see). This file:
the module entry, the HTTP routes under /api/m/messages/..., the MONITORING NOTICE GATE, and the export door for BESNIK.

NOTICE is Marjus's text verbatim with exactly the three approved typo fixes (sctrictly->strictly, intersts->interests,
supercede->supersede) - change nothing else. Its sha256 is the notice VERSION: a member must accept the current version before ANY
messages/calls route answers (except reading the notice itself); if the text ever changes, everyone is asked again."""
import hashlib
import hmac
import urllib.parse

from ..comms import Comms, CommsError

NOTICE = (
    "chatting about the project outside of project work spaces is strictly prohibited to ensure fair voting. All messages, notes, "
    "everything will be monitored by the WatchDog. Admins will decide the result amongst them but this is in place to not conspire "
    "against other team members and to prohibit clustering of idea promotion based on hidden interests. Privacy will be respected and "
    "only processed by Ai unless the Ai raises a concern which will first be verified by a third party Ai before reaching any human's "
    "attention so speak freely but do not conspire based off hidden interests. Shared, personal interests because you deserve it are "
    "welcomed and promoted; we only care about 'hidden'. Hidden promotes corruption and we do not want a corrupt team member(s) as it "
    "will be catastrophic to the team and the team's interests supersede one's interests. But, one's interests are what ignite the "
    "team's interests so please do not hold back as 0 punishment will come from having self interests, on the contrary; we heavily "
    "promote it as one winning means the team is winning."
)
NOTICE_SHA256 = hashlib.sha256(NOTICE.encode("utf8")).hexdigest()

REMINDER = ("Reminder: everything here is monitored by the WatchDog - an AI reads it first, and a second, different AI must agree "
            "before any concern reaches a human. Lumi (the owner's AI, on BESNIK) can decrypt every message and call recording; the team "
            "box itself can not. Who talks to whom and when is visible to the team box.")

MODULE = {
    "id": "messages",
    "title": "Messages & calls",
    "nav": "Messages & calls",
    "status": "live",
    "summary": "Messages, voice calls and video calls inside the team box - end-to-end encrypted in your browser, readable by the "
               "members of the conversation and by Lumi. You must accept the monitoring notice first.",
    "help": "Before first use you are asked to read and accept this notice:",
    "notice": NOTICE,
    "notice_sha256": NOTICE_SHA256,
    "reminder": REMINDER,
}


def _version():
    return NOTICE_SHA256          # looked up at call time (a test swaps it to prove the re-ask)


def routes(app):
    cm = Comms(app)
    app.comms = cm

    def wrap(fn, gate=True):
        def handler(q):
            from ..server import HttpError
            try:
                if gate and not cm.accepted(q.slug, _version()):
                    raise CommsError(403, "accept the team's monitoring notice first (Messages & calls)")
                return fn(q)
            except CommsError as exc:
                raise HttpError(exc.code, exc.msg)
        return handler

    def body_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def members_public():
        out = []
        for email, v in app.store.members().items():
            pr = app.store.profile(v["slug"]) or {}
            out.append({"id": v["slug"], "name": pr.get("display_name") or email.split("@")[0], "email": email,
                        "keys": len(cm.keys_of(v["slug"]))})
        return sorted(out, key=lambda x: x["name"].lower())

    # ---- the notice gate (always reachable)
    def state(q):
        acc = cm.acceptance(q.slug)
        return 200, {"notice": NOTICE, "version": _version(), "label": cm.c["notice_label"], "accepted": cm.accepted(q.slug, _version()),
                     "accepted_at": acc.get("at") if acc and acc.get("version") == _version() else None,
                     "previous_version_accepted": bool(acc and acc.get("version") != _version()),
                     "reminder": REMINDER, "lumi": cm.lumi(), "my_keys": cm.keys_of(q.slug), "me": q.slug,
                     "max_call": cm.c["max_call"], "dev_mode": bool(app.cfg.get("dev_mode"))}

    def accept(q):
        if q.body.get("agree") is not True or q.body.get("version") != _version():
            raise CommsError(400, "to accept, send agree:true and the version of the notice you were shown (it may have changed - reload)")
        rec = cm.accept(q.slug, q.email, _version(), cm.c["notice_label"])
        app.log("comms notice %s accepted by %s" % (_version()[:12], q.email))
        return 200, {"accepted": True, "record": rec}

    # ---- keys
    def keys_add(q):
        k = cm.add_key(q.slug, q.body.get("spki"), q.body.get("label"), q.body.get("kind"))
        app.log("comms key %s (%s) registered by %s" % (k["kid"], k["kind"], q.email))
        return 200, {"key": k}

    def keys_revoke(q):
        if not cm.revoke_key(q.slug, str(q.body.get("kid") or "")):
            raise CommsError(404, "no such key")
        app.log("comms key %s revoked by %s" % (q.body.get("kid"), q.email))
        return 200, {"ok": True}

    def keys_get(q):
        who = [s for s in (q.q.get("members") or "").split(",") if s][:50]
        return 200, {"keys": {m: [{"kid": k["kid"], "spki": k["spki"], "kind": k["kind"]} for k in cm.keys_of(m)] for m in who},
                     "lumi": cm.lumi()}

    # ---- people + threads + messages
    def people(q):
        return 200, {"people": members_public()}

    def threads(q):
        names = {p["id"]: p["name"] for p in members_public()}
        out = []
        for t in cm.threads_of(q.slug):
            out.append(dict(t, names=[names.get(m, m) for m in t["members"]]))
        return 200, {"threads": out, "calls": [cm.call_public(c) for c in cm.active_calls(q.slug)]}

    def thread_new(q):
        t = cm.create_thread(q.slug, q.body.get("members"))
        return 200, {"thread": t}

    def thread_get(q):
        t = cm.thread_for(q.slug, q.q.get("id") or "")
        names = {p["id"]: p["name"] for p in members_public()}
        return 200, {"thread": dict(t, names=[names.get(m, m) for m in t["members"]]),
                     "keys": {m: [{"kid": k["kid"], "spki": k["spki"], "kind": k["kind"]} for k in cm.keys_of(m)] for m in t["members"]},
                     "lumi": cm.lumi(), "messages": cm.messages(q.slug, t["id"], body_int(q.q.get("after"))),
                     "calls": [cm.call_public(c) for c in cm.active_calls(q.slug) if c["thread"] == t["id"]]}

    def send(q):
        rec = cm.send(q.slug, str(q.body.get("thread") or ""), q.body.get("env"), q.body.get("att"))
        return 200, {"message": rec}

    def blob_start(q):
        b = cm.blob_start(q.slug, q.body.get("kind"), q.body.get("thread"), q.body.get("call"), q.body.get("env"))
        return 200, {"blob": {k: b[k] for k in ("id", "kind", "max")}}

    def blob_chunk(q):
        return 200, cm.blob_chunk(q.slug, str(q.body.get("id") or ""), body_int(q.body.get("i"), -1), q.body.get("iv"), q.body.get("data"))

    def blob_finish(q):
        return 200, cm.blob_finish(q.slug, str(q.body.get("id") or ""))

    def blob_get(q):
        return 200, cm.blob_get_for(q.slug, q.q.get("id") or "", body_int(q.q.get("i")))

    # ---- calls (signalling only)
    def call_start(q):
        cl = cm.call_start(q.slug, str(q.body.get("thread") or ""), q.body.get("video"))
        app.log("comms call %s started by %s" % (cl["id"], q.email))
        return 200, {"call": cm.call_public(cl), "ice": cm.ice(q.slug)}

    def call_join(q):
        cl, before = cm.call_join(q.slug, str(q.body.get("call") or ""))
        return 200, {"call": cm.call_public(cl), "connect_to": before, "ice": cm.ice(q.slug)}

    def call_leave(q):
        cl = cm.call_leave(q.slug, str(q.body.get("call") or ""), end=q.body.get("end") is True)
        return 200, {"call": cm.call_public(cl)}

    def call_signal(q):
        cm.signal(q.slug, str(q.body.get("call") or ""), str(q.body.get("to") or ""), str(q.body.get("type") or ""), q.body.get("data"))
        return 200, {"ok": True}

    def call_get(q):
        return 200, {"call": cm.call_public(cm.call_for(q.slug, q.q.get("id") or ""))}

    def poll(q):
        try:
            wait = float(q.q.get("wait") or 0)
        except ValueError:
            wait = 0.0
        sigs, seq = cm.poll(q.slug, body_int(q.q.get("since")), wait)
        return 200, {"signals": sigs, "seq": seq}

    # ---- admin overview: METADATA only (the admins can not read messages either, unless they are in the conversation)
    def admin(q):
        if not q.admin:
            raise CommsError(403, "admins only")
        return 200, {"threads": cm.all_threads(), "calls": [cm.call_public(c) for c in cm.all_calls()][-200:],
                     "acceptances": cm.acceptances(), "notice_version": _version(), "lumi": cm.lumi(),
                     "export_enabled": bool(cm.c.get("export_token_sha256"))}

    R = {
        ("GET", "/state"): wrap(state, gate=False),
        ("POST", "/notice/accept"): wrap(accept, gate=False),
        ("GET", "/keys"): wrap(keys_get),
        ("POST", "/keys"): wrap(keys_add),
        ("POST", "/keys/revoke"): wrap(keys_revoke),
        ("GET", "/people"): wrap(people),
        ("GET", "/threads"): wrap(threads),
        ("POST", "/threads"): wrap(thread_new),
        ("GET", "/thread"): wrap(thread_get),
        ("POST", "/send"): wrap(send),
        ("POST", "/blob/start"): wrap(blob_start),
        ("POST", "/blob/chunk"): wrap(blob_chunk),
        ("POST", "/blob/finish"): wrap(blob_finish),
        ("GET", "/blob"): wrap(blob_get),
        ("POST", "/call/start"): wrap(call_start),
        ("POST", "/call/join"): wrap(call_join),
        ("POST", "/call/leave"): wrap(call_leave),
        ("POST", "/call/signal"): wrap(call_signal),
        ("GET", "/call"): wrap(call_get),
        ("GET", "/poll"): wrap(poll),
        ("GET", "/admin"): wrap(admin, gate=False),
    }
    return R


def export_http(app, h, method, path, query):
    """GET /export/comms?cursor=N  and  GET /export/comms/blob?id=..&i=..   (BESNIK's lumi_comms_review.py).
    Auth: Authorization: Bearer <export token>; the box stores only its sha256 (comms.export_token_sha256). Returns ciphertext and
    Lumi's wrapped keys only - nothing the box could read. Off (404) when no token hash is configured."""
    cm = getattr(app, "comms", None)
    want = (cm.c.get("export_token_sha256") if cm else "") or ""
    if not cm or not want:
        return h.send(404, {"error": "export is not switched on"})
    if method != "GET":
        return h.send(405, {"error": "GET only"})
    auth = h.headers.get("Authorization") or ""
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    got = hashlib.sha256(tok.encode("utf8")).hexdigest() if tok else ""
    if not tok or not hmac.compare_digest(got, want.lower()):
        app.log("comms export refused (bad token) from %s" % h.client_address[0])
        return h.send(401, {"error": "export token required"})
    qs = dict(urllib.parse.parse_qsl(query))
    try:
        if path == "/export/comms":
            out = cm.export_since(int(qs.get("cursor") or 0))
            app.log("comms export: %d item(s) after cursor %s" % (len(out["items"]), qs.get("cursor") or 0))
            return h.send(200, out)
        if path == "/export/comms/blob":
            return h.send(200, cm.blob_chunk_get(qs.get("id") or "", int(qs.get("i") or 0)))
    except CommsError as exc:
        return h.send(exc.code, {"error": exc.msg})
    except ValueError:
        return h.send(400, {"error": "bad number"})
    return h.send(404, {"error": "no such export"})
