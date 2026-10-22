"""LUMI'S WELCOME (Marjus, 2026-10-21: "person starts talking to lumi ... she lets them know where they can do what by introducing
herself based on what i said they're there to do. and that starts their first project").

After setup (invite code -> PIN -> vault password -> optional profile) the member lands in a chat with Lumi. That chat goes to the
team's SHARED model through BESNIK's team gateway (under the member's own account and caps), with the system prompt from
config/templates/lumi-welcome.txt filled in with the PURPOSE Marjus gave in the invite and the site's areas with their links.
Their FIRST PROJECT (a private sandbox named from the purpose) is created when the account is made. If no model answers, the member
gets the plain fallback text (config/templates/lumi-welcome-fallback.txt) instead, which says why."""
import os
import re
import string
import time
import unicodedata

from . import config as C
from . import store as S
from .sandboxes import SandboxError

AREAS = [
    ("Home feed", "#/", "the team's posts and files, comments and emoji; work people chose to share"),
    ("Projects", "#/projects", "your private project spaces (git repositories) - only you and the admins see them"),
    ("AI builds", "#/builds", "let a shared AI build something in one of your projects; tests run in a locked jail"),
    ("Models & chat", "#/models", "chat with the team's AI models and save your own settings versions"),
    ("Your own AI tool", "#/byo", "connect Claude Code, Codex, Cursor or OpenCode to a project with a git token"),
    ("Vault", "#/vault", "your personal encrypted notes and files, opened with your vault password"),
    ("Messages & calls", "#/m/messages", "encrypted messages, calls and video calls with the team"),
    ("Tour", "#/tour", "a one-minute overview with a checkpoint for each area"),
    ("How it works", "/how-it-works", "the public page that explains every claim and how to check it"),
]
STOP = set("a an the and or to of for in on at with by from about into as is are be been being it its this that these those "
           "they their them he she his her we our you your i me my here there help helps helping do does doing make making work "
           "working build building team our new some any all will would can could should just also very really get go going "
           "who what when where why how they're theyre there's".split())


def project_name(purpose):
    s = unicodedata.normalize("NFKD", str(purpose or "")).encode("ascii", "ignore").decode().lower().replace("'", "")
    words = [w for w in re.findall(r"[a-z0-9]+", s) if w not in STOP and len(w) > 1]
    name = "-".join(words[:4])[:40].strip("-")
    return name or "first-project"


def project_label(purpose):
    p = re.sub(r"\s+", " ", str(purpose or "")).strip()
    if not p:
        return "First project"
    p = p[0].upper() + p[1:]
    return p if len(p) <= 60 else p[:57].rstrip() + "..."


def ensure_first_project(app, slug, purpose):
    """Create the member's first project from the purpose. -> (name, label). Never raises for "already exists"."""
    name, label = project_name(purpose), project_label(purpose)
    if not app.sb.exists(slug, name):
        try:
            app.sb.create(slug, name, label)
        except SandboxError as exc:
            if "already" not in str(exc):
                name = "first-project"
                if not app.sb.exists(slug, name):
                    app.sb.create(slug, name, label)
    return name, label


def _read(rel):
    p = rel if os.path.isabs(rel) else os.path.join(C.HERE, rel)
    with open(p, encoding="utf8") as fh:
        return fh.read()


def render(cfg, which, **kw):
    wc = cfg.get("welcome") or {}
    tpl = _read(wc.get(which) or C.DEFAULTS["welcome"][which])
    return string.Template(tpl).safe_substitute(**kw)


def areas_text(public_url):
    u = public_url.rstrip("/")
    return "\n".join("   - %s: %s - %s" % (t, d, u + "/" + link if link.startswith("#") else u + link) for t, link, d in AREAS)


def pick_model(app, account):
    wc = app.cfg.get("welcome") or {}
    if wc.get("model"):
        return wc["model"], ""
    if app.cfg["builds"].get("default_model"):
        return app.cfg["builds"]["default_model"], ""
    status, j, why = app.gw.call("GET", "/team/models", account, timeout=20)
    if status != 200 or not j:
        return "", why or "the team gateway did not answer"
    models = j.get("models") or []
    running = [m for m in models if m.get("state") in ("running", "ready", "on", "up")]
    m = (running or models or [None])[0]
    if not m:
        return "", "no model is shared with your account yet"
    return m.get("id") or "", ""


def start(app, slug, account_id):
    """Idempotent: the member's Lumi welcome chat (made once). -> the chat."""
    pr = app.store.profile(slug) or {}
    w = pr.get("welcome") or {}
    if w.get("chat"):
        c = app.store.chat(slug, w["chat"])
        if c:
            return c
    acc = app.auth.account(account_id) or {}
    purpose = acc.get("purpose") or pr.get("purpose") or "work with the team"
    fp = pr.get("first_project") or {}
    if not fp.get("name") or not app.sb.exists(slug, fp["name"]):
        n, lab = ensure_first_project(app, slug, purpose)
        fp = {"name": n, "label": lab}
        pr["first_project"] = fp
    u = app.cfg["public_url"].rstrip("/")
    kw = {"name": pr.get("display_name") or acc.get("name") or "there", "purpose": purpose, "language": pr.get("language") or "en",
          "project_label": fp.get("label") or fp["name"], "project_link": "%s/#/project/%s/%s" % (u, slug, fp["name"]),
          "areas": areas_text(u), "public_url": u}
    system = render(app.cfg, "template", **kw)
    model, why = pick_model(app, account_id)
    c = app.store.new_chat(slug, model or "(no model)", "original")
    c.update(title="Lumi - welcome", lumi=True, system_prompt=system)
    via = "fallback"
    if model:
        status, j, why2 = app.gw.call("POST", "/v1/chat/completions", account_id,
                                      {"model": model, "messages": [{"role": "system", "content": system},
                                                                    {"role": "user", "content": "(I just finished setting up my account. Please write your first message to me.)"}],
                                       "max_tokens": 700})
        ans = ((j or {}).get("choices") or [{}])[0].get("message", {}).get("content") if status == 200 and j else ""
        if ans:
            c["messages"].append({"role": "assistant", "content": ans.strip(), "at": S.now_s(), "lumi": True})
            via = "model"
        else:
            why = why2 or "the model gave no answer"
    if via == "fallback":
        c["messages"].append({"role": "assistant", "content": render(app.cfg, "fallback", why=why, **kw).strip(), "at": S.now_s(),
                              "lumi": True, "fallback": True})
    app.store.save_chat(slug, c)
    pr = app.store.profile(slug) or pr
    pr["first_project"] = fp
    pr["welcome"] = {"chat": c["id"], "at": S.now_s(), "model": model, "via": via, "t": time.time()}
    app.store.save_profile(slug, pr)
    app.log("welcome: %s -> chat %s via %s (%s)" % (account_id, c["id"], via, model or why))
    return c
