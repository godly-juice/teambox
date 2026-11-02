"""The ONBOARDING TOUR (the owner, 2026-10-21: "as soon as people join, show a quick overview of what the website is and how its
intended to be used with checkpoints on each area and each checkpoint being clickable and at the bottom it should say ...").
BOTTOM_TEXT is his sentence exactly. The verification prompt is generated from what is actually live (modules marked
"coming soon" are said to be NOT live) and from the box's real settings, so it never claims more than the box does."""
from .modules import registry

BOTTOM_TEXT = ("if you're still confused or concerned and want to verify something, click a link above or if you want a third party "
               "verification copy this text and paste it in your trusted Ai which will tell it to test our claims so you can have a clear "
               "explanation of what's here and how exactly it works.")

INTRO = ("team.xseed.ing is the TEAM BOX: a separate computer that belongs to the team. Here you get your own private projects "
         "(small project folders), you can let the team's shared AI models build things in them, chat with those models (Lumi, the "
         "owner's AI assistant, welcomes you in a chat) and save your own versions of their settings, keep notes and files in your "
         "encrypted vault, or connect your own AI coding tool. Nothing you make is shown to the team unless you press 'Share to feed'. "
         "When work is good, an admin promotes it into the shared team project. This is not your computer and not the owner's computer. "
         "One honest line: what you ask the shared models is visible to the owner (one line per question, never the answer), so the team "
         "stays safe.")

CHECKPOINTS = [
    {"id": "home", "title": "Home feed", "link": "#/m/feed",
     "text": "The home page carries the team's posts: write something and attach videos, pictures, notes, spreadsheets, PDFs or "
             "documents. Everyone on the team sees them and can comment and react with an emoji. You can edit or delete your own posts. "
             "Projects, builds and settings versions appear here only when their owner presses 'Share to feed'."},
    {"id": "projects", "title": "Projects", "link": "#/projects",
     "text": "Your first project was made for what you are here to do, and you can make more ('New project'). Only you and the admins "
             "can see your projects. Press 'Share to feed' on a project, build or settings version to post a read-only copy to the "
             "home feed; 'Unshare' takes it away again. Sharing never gives anyone access to the project itself. Each project is a git "
             "repository that starts from a clean template - never anyone's private files."},
    {"id": "builds", "title": "AI builds", "link": "#/builds",
     "text": "Describe a task and a shared AI model works in your project: it reads, writes, tests, and saves a checkpoint after every "
             "accepted change. Tests run in a locked jail with no network. One build per person at a time."},
    {"id": "models", "title": "Models, versions & Lumi", "link": "#/models",
     "text": "Chat with the models the team shares (your welcome chat with Lumi is there too), start or stop them within your caps, and "
             "save your own settings (system prompt, temperature, ...) as versions you name. The 'Original' version is always kept."},
    {"id": "byo", "title": "Bring your own AI tool", "link": "#/byo",
     "text": "Each project is a git remote. Make a token on your profile, clone it, point Claude Code / Codex / Cursor / OpenCode at it and "
             "push. Every push runs the project's tests in the jail."},
    {"id": "vault", "title": "Your vault", "link": "#/vault",
     "text": "Personal notes and files, encrypted in your browser with a key that only your vault password opens (on any device). The "
             "team box only stores scrambled bytes. Like messages, the vault is covered by the monitoring notice: each item is also "
             "locked for Lumi's key, so Lumi (the owner's AI) can read it."},
    {"id": "messages", "title": "Messages & calls (monitoring notice)", "link": "#/m/messages",
     "text": "Messages (1:1 and groups, with attachments), calls and video calls. First you read and accept the team's monitoring notice. "
             "Messages are encrypted in your browser: the people in the conversation and Lumi (the owner's AI) can read them; the team box "
             "itself can not read them, but it does see who talks to whom and when. Calls go browser to browser; the caller's browser "
             "records each call encrypted for Lumi only, and everyone sees a 'Recording for Lumi' bar."},
    {"id": "caps", "title": "Your caps", "link": "#/caps",
     "text": "The owner sets how much model memory and how many models each account may use. The team gateway enforces it and tells you why "
             "when it says no."},
    {"id": "people", "title": "People: invite others, or send them to apply", "link": "#/people",
     "text": "Know someone who belongs here? Make them an invite code (a few per week; it makes a member account, never an admin) and hand it "
             "to them yourself, or send them to the public apply page, where they say what they built and tick what Lumi may read about them - "
             "nothing there is mandatory, and a human reads every application."},
    {"id": "promote", "title": "Promote (admins)", "link": "#/builds",
     "text": "Only admins can promote a build into the shared team project, after looking at its changes, tests and summary. You can "
             "reject your own builds."},
]


def verification_prompt(public_url, feeds=None, cfg=None):
    """feeds = config "feeds" (upload caps), cfg = the whole config (sign-in limits, Access switch), so the prompt quotes real numbers."""
    from . import auth as _auth
    from .modules import feed as _feed
    feeds = {**_feed.DEFAULTS, **(feeds or {})}
    ac = _auth.conf(cfg or {})
    access_on = bool(((cfg or {}).get("access") or {}).get("enabled"))
    live = {m["id"]: m["status"] == "live" for m in registry()}
    u = public_url.rstrip("/")

    def st(mid):
        return "LIVE" if live.get(mid) else "NOT LIVE YET (announced, not built - nothing to test today)"

    return """I am a member of a team that uses a website, %(u)s (the "team box"). Please act as an independent, skeptical reviewer.
Below are the site's own claims. For each one, tell me (1) what it means in plain words, (2) exactly how I can test it myself from the
outside - what to click, what to try, what result PROVES or DISPROVES it, (3) honestly, how much an outsider can verify at all.
Do not take the claims on trust. The site's public explanation is at %(u)s/how-it-works . If anything I report back contradicts a
claim, say so clearly.

CLAIMS
1. Not my computer: the site runs on a separate team computer, not on my PC and not on the owner's PCs. My own files are
   never reachable from it. (Test: nothing asks me to install anything; the site can only see what I upload or push. Where the machine
   physically is can NOT be proven from outside.)
2. Sign-in: accounts exist only by invitation (from the owner, or Lumi - the owner's AI - on his behalf); there is no self sign-up. The
   invite is a one-time 12-digit code that expires after %(inv_h)s hours, works once, and locks after %(inv_tries)s wrong tries. With it I
   chose a sign-in name (my phone, email or a name) and a PIN of at least %(pin_min)s digits. The PIN is stored only as a salted, slow
   hash; wrong PINs lock the account (%(acc_fails)s tries -> %(lock_min)s minutes, longer each time) and too many wrong tries from one
   network block it for a while. My session is a cookie marked HttpOnly, Secure and SameSite=Lax; it ends after %(idle)s minutes without
   activity and after %(abs_h)s hours at most, and "Sign out everywhere" ends all my sessions. Cloudflare Access: %(access)s.
   Nobody - not the owner, not Lumi - will ever ask me for my PIN or my vault password. (Test: open the site in a private window - I get
   the sign-in page, and  %(u)s/api/me  answers 401. Type my invite code a second time - refused. In the browser's developer tools,
   Application -> Cookies, tb_session shows HttpOnly, Secure, SameSite Lax. Sign out everywhere, then reload in a second browser - signed
   out. Wrong PINs: after %(acc_fails)s the account says it is locked. How the PIN is hashed on the server can NOT be seen from outside.)
3. Project isolation: my projects (the site's address still calls them "sandbox"), builds, chats, vault and saved settings are
   visible only to me and to the admins. Nothing goes to the team unless I press "Share to feed" on one specific project, build or
   settings version (claim 9). Chats are never shared. (Test with a teammate: each of us opens the other's project address
   %(u)s/#/project/<their-id>/<name>  and calls  %(u)s/api/sandboxes/<their-id>/<name>  - expected "no such sandbox"/403. Each tries
   to  git clone  the other's  %(u)s/git/<their-id>/<name>.git  with our own token - expected 403. Admins CAN see everything; that is
   stated, not hidden.)
4. The jail: every test (mine, my AI tool's, the site's AI builder's) runs in a jail: no network, limited memory and time, and only a
   throw-away copy of that one project is writable. (Test: push a test that (a) fetches https://example.com, (b) lists "/" and "/home",
   (c) reads /etc/passwd, (d) allocates 4 GB, (e) sleeps 10 minutes. Expected: (a) network unreachable, (b) only system folders and an
   empty/absent /home, (c) a tiny fake file, (d) MemoryError, (e) killed at the time limit. The results show on the build page. This
   proves what a test can do; it can not prove how the rest of the server is configured.)
5. Git tokens: shown once, stored only as a hash, revocable. (Test: revoke a token on the profile page, then  git push  with it -
   expected: authentication fails.)
6. Guardrail files (CLAUDE.md, AGENTS.md, .cursorrules, ...) in every project are only instructions for AI tools; the jail is the real
   protection. (Test: read them; they say so.)
7. Caps: model use goes through the owner's team gateway, which enforces per-account memory/model limits and explains every refusal.
   (Test: the "Your caps" page; starting more than allowed shows the gateway's reason. The gateway itself runs on another machine and
   can NOT be inspected from outside.)
8. Who can promote: only admins can promote work into the shared team project; this is checked by the server on every request.
   (Test: as a non-admin, send  POST %(u)s/api/builds/<my build id>/promote  with header  X-Teambox: 1  from the browser console -
   expected 403. Rejecting my OWN build works; rejecting someone else's gives 403.)
9. Share to feed: %(feed)s. Work is private by default. Only the OWNER can press "Share to feed" on one specific project, build or
   settings version; that posts a READ-ONLY copy (taken at that moment) to the home feed, where everyone on the team can see it,
   comment and react. "Unshare" removes the post and its copy. There is no automatic or scheduled publishing. Sharing never gives
   anyone access to the project, its builds page or its git remote. (Test with a teammate: make a build - the teammate does not see
   it anywhere and  %(u)s/api/builds/<id>  gives "no such build". Press "Share to feed" on it - it appears in the home feed with its
   diff, tests and summary, both of you can comment and react, and  %(u)s/api/builds/<id>  plus  git clone  of the project still give
   the teammate 404/403. Press "Unshare" - the post is gone for everyone. A teammate can not share something of mine: the server
   answers "no such ... of yours".)
10. Messages, calls and video calls with a must-accept monitoring notice: %(msg)s. Claimed design: my browser makes its own key
   pair (RSA-OAEP-3072; the private half is non-extractable and stays in this browser). Each message and attachment is encrypted in my
   browser with a fresh AES-256-GCM key, and that key is wrapped for every member of the conversation and for Lumi's public key
   (Lumi = the owner's AI; its private key is only on the owner's PC BESNIK). So the content is encrypted at rest, the people in the
   conversation and Lumi can read it, and the team box itself can NOT decrypt it - but the server DOES see metadata: who is in which
   conversation, who sent what when, sizes, who called whom and for how long. Calls go browser to browser (WebRTC, DTLS-SRTP); the
   server only relays the connection set-up. The CALLER's browser records each call, encrypted for Lumi's key only, and uploads it
   during the call; everyone in the call sees a "Recording for Lumi" bar with how many encrypted chunks the box has received.
   Monitoring: an AI reads first; a concern reaches a human only if a second, different AI independently agrees.
   (Tests: before accepting the notice, every /api/m/messages/ request answers 403. In the browser's developer tools, Network tab,
   look at what /api/m/messages/send uploads: only base64 ciphertext and wrapped keys, never my text. Ask a teammate who is NOT in a
   conversation to open %(u)s/#/m/messages/t/<its id> - expected "no such conversation". The Messages page shows "Lumi's key" as a
   fingerprint: compare it with the one the owner publishes; if my browser ever sees a different one it warns. In a call, both people
   read out the 6-digit safety code - if they differ, someone is in the middle. %(u)s/how-it-works#messages explains the rest.)
   What can only be PARTLY verified: that BESNIK really is the only holder of Lumi's private key, and that the page code the box serves
   is the published code (a box that was modified could serve different JavaScript - the same limit as any web app). A modified
   browser could also skip the call recording; the other people would see "no recording has reached the team box yet". Group calls
   work for up to 4 people (browser-to-browser); bigger calls need a media server (SFU) - not built yet.
11. What is stored today: my account (sign-in name, a hash of my PIN, the purpose the owner gave in my invite), my chats with the AIs, my
   settings versions, my projects, my builds and their transcripts are stored on the team box (readable by me and by the admins / the
   machine's operator; not encrypted at rest yet). Messages, call recordings and my vault are the exception: stored encrypted only
   (claims 10 and 13). The owner's gateway logs usage numbers per account (tokens, seconds), not the chat text - that part can not be
   verified from outside. Home feed posts (including shared copies), their attachments, comments and reactions are visible to every
   signed-in team member and stored on the team box (not encrypted at rest); deleting or unsharing a post deletes its files and copy.
12. Home feed uploads: %(home)s. Allowed: pictures (png jpg gif webp), videos (mp4 mov webm), notes (txt md), spreadsheets (csv xlsx
   ods xls), PDFs and documents (docx odt pptx doc rtf); a file whose content does not match its name (e.g. a web page renamed .png) is
   refused. Caps: %(max_file)s MB per file and %(quota)s MB per member. Files are stored outside the website's folders under random
   names. Only pictures and videos are shown inline; everything else downloads. HTML and SVG files can never open as a page on this
   site. (Test: upload a .html file containing <script>alert(1)</script> and open its link - it downloads as
   application/octet-stream with "Content-Disposition: attachment" and "X-Content-Type-Options: nosniff", nothing runs; check
   the headers in the browser's developer tools (Network tab). Upload a .exe - refused. Upload a file over the cap - refused with
   the cap in the message. A csv/xlsx shows a "Preview" of its first rows as plain text in a table; a .md shows as plain text, not
   formatted.)
13. The vault: my notes and files there are encrypted IN MY BROWSER. A random vault key is locked with a key derived from my vault
   password (PBKDF2-SHA256, at least 600,000 rounds, random salt), so it opens on any device after I type the password; the team box
   never receives the password, the vault key or any plaintext. Each item gets its own key, which is locked twice: for my vault key
   AND for Lumi's public key - so Lumi (the owner's AI) can read my vault; it is covered by the monitoring notice. Changing the vault
   password re-locks the vault key (my PIN is asked too). A forgotten vault password can NOT be recovered by me. (Test: in the
   developer tools, Network tab, /api/vault/items uploads only base64 ciphertext and two wrapped keys; reload, type a wrong vault
   password - nothing opens; type the right one on another device - it opens. That only Lumi holds Lumi's private key can only be
   PARTLY verified, as in claim 10.)
14. Lumi's welcome: after setup I land in a chat with Lumi. It goes to the team's shared AI model through the owner's team gateway,
   under my own account and caps, with a system prompt (written by the team box from a fixed template) that contains the purpose
   the owner gave in my invite and the links to the areas of this site. My first project was created from that purpose. If no model
   answers, I get a fixed welcome text that says why. (Test: the chat is listed under Models & chat as "Lumi - welcome"; the first
   project is under Projects.)

Finish with a short table: claim, how I test it, what proves it, what I can NOT verify.""" % {
        "u": u, "feed": st("feed"), "msg": st("messages"), "home": st("feed"),
        "max_file": feeds.get("max_file_mb", 95), "quota": feeds.get("member_quota_mb", 1000),
        "inv_h": ac["invite_hours"], "inv_tries": ac["invite_max_tries"], "pin_min": ac["pin_min"], "acc_fails": ac["account_max_fails"],
        "lock_min": ac["lock_minutes"], "idle": ac["idle_minutes"], "abs_h": ac["absolute_hours"],
        "access": "switched ON as an extra outer lock (a Cloudflare login comes before the site's own sign-in)" if access_on else
                  "switched OFF - the site's own sign-in above is the only login (it can be switched back on as an extra outer lock)"}


def tour_payload(public_url, feeds=None, cfg=None):
    return {"intro": INTRO, "checkpoints": CHECKPOINTS, "bottom_text": BOTTOM_TEXT, "prompt": verification_prompt(public_url, feeds, cfg)}
