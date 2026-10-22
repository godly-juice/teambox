"""The ONBOARDING TOUR (Marjus, 2026-10-21: "as soon as people join, show a quick overview of what the website is and how its
intended to be used with checkpoints on each area and each checkpoint being clickable and at the bottom it should say ...").
BOTTOM_TEXT is his sentence exactly. The verification prompt is generated from what is actually live (modules marked
"coming soon" are said to be NOT live), so it never claims more than the box does."""
from .modules import registry

BOTTOM_TEXT = ("if you're still confused or concerned and want to verify something, click a link above or if you want a third party "
               "verification copy this text and paste it in your trusted Ai which will tell it to test our claims so you can have a clear "
               "explanation of what's here and how exactly it works.")

INTRO = ("team.xseed.ing is the TEAM BOX: a separate computer that belongs to the team. Here you get your own private sandboxes "
         "(small project folders), you can let the team's shared AI models build things in them, chat with those models and save your "
         "own versions of their settings, or connect your own AI coding tool. When work is good, an admin promotes it into the shared "
         "team project. This is not your computer and not Marjus's computer.")

CHECKPOINTS = [
    {"id": "home", "title": "Home feed", "link": "#/m/feed",
     "text": "The home page carries the team's posts: write something and attach videos, pictures, notes, spreadsheets, PDFs or "
             "documents. Everyone on the team sees them and can comment and react with an emoji. You can edit or delete your own posts."},
    {"id": "sandboxes", "title": "Sandboxes", "link": "#/sandboxes",
     "text": "You get a sandbox of your own and can make more ('New sandbox'). Only you and the admins can see yours (the Tuesday team "
             "feed shows read-only copies of your work, never the sandbox itself). Each one is a git "
             "repository that starts from a clean template - never anyone's private files."},
    {"id": "builds", "title": "AI builds", "link": "#/builds",
     "text": "Describe a task and a shared AI model works in your sandbox: it reads, writes, tests, and saves a checkpoint after every "
             "accepted change. Tests run in a locked jail with no network. One build per person at a time."},
    {"id": "models", "title": "Models & versions", "link": "#/models",
     "text": "Chat with the models the team shares, start or stop them within your caps, and save your own settings (system prompt, "
             "temperature, ...) as versions you name. The 'Original' version is always kept."},
    {"id": "byo", "title": "Bring your own AI tool", "link": "#/byo",
     "text": "Each sandbox is a git remote. Make a token on your profile, clone it, point Claude Code / Codex / Cursor / OpenCode at it and "
             "push. Every push runs the sandbox's tests in the jail."},
    {"id": "team-feed", "title": "Team feed (Tuesdays 14:00)", "link": "#/m/team-feed",
     "text": "Every Tuesday at 2 pm (team box time) everyone's work and models of the week - builds, commits, new sandboxes, settings "
             "versions - become visible to the whole team as read-only copies, with comments and emoji reactions. Until then your work "
             "is visible only to you and the admins."},
    {"id": "messages", "title": "Messages & calls (monitoring notice)", "link": "#/m/messages",
     "text": "Messages (1:1 and groups, with attachments), calls and video calls. First you read and accept the team's monitoring notice. "
             "Messages are encrypted in your browser: the people in the conversation and Lumi (the owner's AI) can read them; the team box "
             "itself can not read them, but it does see who talks to whom and when. Calls go browser to browser; the caller's browser "
             "records each call encrypted for Lumi only, and everyone sees a 'Recording for Lumi' bar."},
    {"id": "caps", "title": "Your caps", "link": "#/caps",
     "text": "Marjus sets how much model memory and how many models each account may use. The team gateway enforces it and tells you why "
             "when it says no."},
    {"id": "promote", "title": "Promote (admins)", "link": "#/builds",
     "text": "Only admins can promote a build into the shared team project, after looking at its changes, tests and summary. You can "
             "reject your own builds."},
]


def verification_prompt(public_url, feeds=None):
    """feeds = config "feeds" (timezone, upload caps), so the prompt quotes the box's real numbers."""
    from .modules import feed as _feed, team_feed as _tf
    feeds = {**_tf.DEFAULTS, **_feed.DEFAULTS, **(feeds or {})}
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
1. Not my computer: the site runs on a separate team computer, not on my PC and not on the owner's (Marjus's) PCs. My own files are
   never reachable from it. (Test: nothing asks me to install anything; the site can only see what I upload or push. Where the machine
   physically is can NOT be proven from outside.)
2. Sign-in: Cloudflare Access email login; my identity on the site is that email. (Test: open the site in a private window - I should
   get an email login first; /api/me shows only my email and role.)
3. Sandbox isolation: my sandboxes, builds, chats and saved settings are visible only to me and to the admins - except that the weekly
   team feed (claim 9) shows the team read-only COPIES of my finished builds, commits, new sandboxes' file lists and settings
   versions after each Tuesday 14:00. Chats are never published. (Test with a teammate:
   each of us opens the other's sandbox address  %(u)s/#/sandbox/<their-id>/<name>  and calls  %(u)s/api/sandboxes/<their-id>/<name>
   - expected "no such sandbox"/403. Each tries to  git clone  the other's  %(u)s/git/<their-id>/<name>.git  with our own token -
   expected 403. Admins CAN see everything; that is stated, not hidden.)
4. The jail: every test (mine, my AI tool's, the site's AI builder's) runs in a jail: no network, limited memory and time, and only a
   throw-away copy of that one sandbox is writable. (Test: push a test that (a) fetches https://example.com, (b) lists "/" and "/home",
   (c) reads /etc/passwd, (d) allocates 4 GB, (e) sleeps 10 minutes. Expected: (a) network unreachable, (b) only system folders and an
   empty/absent /home, (c) a tiny fake file, (d) MemoryError, (e) killed at the time limit. The results show on the build page. This
   proves what a test can do; it can not prove how the rest of the server is configured.)
5. Git tokens: shown once, stored only as a hash, revocable. (Test: revoke a token on the profile page, then  git push  with it -
   expected: authentication fails.)
6. Guardrail files (CLAUDE.md, AGENTS.md, .cursorrules, ...) in every sandbox are only instructions for AI tools; the jail is the real
   protection. (Test: read them; they say so.)
7. Caps: model use goes through the owner's team gateway, which enforces per-account memory/model limits and explains every refusal.
   (Test: the "Your caps" page; starting more than allowed shows the gateway's reason. The gateway itself runs on another machine and
   can NOT be inspected from outside.)
8. Who can promote: only admins can promote work into the shared team project; this is checked by the server on every request.
   (Test: as a non-admin, send  POST %(u)s/api/builds/<my build id>/promote  with header  X-Teambox: 1  from the browser console -
   expected 403. Rejecting my OWN build works; rejecting someone else's gives 403.)
9. Weekly team feed: %(feed)s. Every Tuesday at 14:00 (team box time, %(tz)s) everything members made before that moment and not
   yet published - finished builds, sandbox commits, new sandboxes, saved settings versions - becomes a READ-ONLY copy in the
   Team feed that every member can see, comment on and react to. Before that, it is visible only to its owner and the admins.
   Publishing never gives anyone access to another member's sandbox, builds or git remote. (Test with a teammate: make a build or
   save a settings version, then before Tuesday 14:00 the teammate opens Team feed - it is not there, and  %(u)s/api/builds/<id>
   gives "no such build". After Tuesday 14:00 it is in the Team feed with its diff, tests and summary, both of you can comment and
   react, and  %(u)s/api/builds/<id>  plus  git clone  of your sandbox still give the teammate 404/403. "Your work that goes out
   next time" on the Team feed page lists exactly what will be published. If the box was off at 14:00 it publishes as soon as it is
   back, marked "published late". Admins can also "publish now"; such a publish is labelled "Early publish".)
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
   fingerprint: compare it with the one Marjus publishes; if my browser ever sees a different one it warns. In a call, both people
   read out the 6-digit safety code - if they differ, someone is in the middle. %(u)s/how-it-works#messages explains the rest.)
   What can only be PARTLY verified: that BESNIK really is the only holder of Lumi's private key, and that the page code the box serves
   is the published code (a box that was modified could serve different JavaScript - the same limit as any web app). A modified
   browser could also skip the call recording; the other people would see "no recording has reached the team box yet". Group calls
   work for up to 4 people (browser-to-browser); bigger calls need a media server (SFU) - not built yet.
11. What is stored today: my chats with the AIs, my settings versions, my builds and their transcripts are stored on the team box under
   my member folder (readable by me and by the admins / the machine's operator; not encrypted at rest yet). Messages and call
   recordings are the exception: stored encrypted only (claim 10). The owner's gateway logs usage numbers per account (tokens,
   seconds), not the chat text - that part can not be verified from outside.
   Home feed posts, their attachments, comments and reactions are visible to every signed-in team member and stored on the team box
   (not encrypted at rest); deleting a post deletes its files.
12. Home feed uploads: %(home)s. Allowed: pictures (png jpg gif webp), videos (mp4 mov webm), notes (txt md), spreadsheets (csv xlsx
   ods xls), PDFs and documents (docx odt pptx doc rtf); a file whose content does not match its name (e.g. a web page renamed .png) is
   refused. Caps: %(max_file)s MB per file and %(quota)s MB per member. Files are stored outside the website's folders under random
   names. Only pictures and videos are shown inline; everything else downloads. HTML and SVG files can never open as a page on this
   site. (Test: upload a .html file containing <script>alert(1)</script> and open its link - it downloads as
   application/octet-stream with "Content-Disposition: attachment" and "X-Content-Type-Options: nosniff", nothing runs; check
   the headers in the browser's developer tools (Network tab). Upload a .exe - refused. Upload a file over the cap - refused with
   the cap in the message. A csv/xlsx shows a "Preview" of its first rows as plain text in a table; a .md shows as plain text, not
   formatted.)

Finish with a short table: claim, how I test it, what proves it, what I can NOT verify.""" % {
        "u": u, "feed": st("team-feed"), "msg": st("messages"), "home": st("feed"), "tz": feeds.get("timezone", "America/Chicago"),
        "max_file": feeds.get("max_file_mb", 95), "quota": feeds.get("member_quota_mb", 1000)}


def tour_payload(public_url, feeds=None):
    return {"intro": INTRO, "checkpoints": CHECKPOINTS, "bottom_text": BOTTOM_TEXT, "prompt": verification_prompt(public_url, feeds)}
