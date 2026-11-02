# Team box (team.xseed.ing)

The team's own computer: invite-only accounts (invite code -> PIN), private per-member projects (git repos; "sandboxes" in the
code and URLs), AI builds through BESNIK's capped team gateway, chat with shared models with named settings versions, Lumi's
welcome chat, a per-member encrypted vault, bring-your-own AI tool over git, "Share to feed", and admin-only promote into the shared
team project. Python 3 standard library + git + bubblewrap. Install: see `INSTALL-XOS.md`.

| Path | What |
|---|---|
| `teambox/server.py` | HTTP app, routes, role checks (members: own things; admins: all; promote: admins only), the admin invite API |
| `teambox/auth.py` | invites (one-time codes), accounts, PINs (scrypt + pepper), sessions, brute-force limits, admin API tokens; CLI `python3 -m teambox.auth` (admin-invite, new-api-token, list, unlock, signout-all) |
| `teambox/identity.py` | who is asking (the session), the optional Cloudflare Access outer lock (RS256 JWT verification), dev mode |
| `teambox/vault.py` + `static/vault.js` | the per-member vault: browser-side encryption, wrapped for the member's vault key (password, PBKDF2) and Lumi's key |
| `teambox/welcome.py` + `config/templates/` | Lumi's welcome chat (system prompt template + fallback) and the first project named from the invite's purpose |
| `teambox/share.py` | "Share to feed" snapshots (project / build / settings version), posted by `modules/feed.py` |
| `teambox/sandboxes.py` | projects from the clean `starter/`, team-main, hardened git config |
| `teambox/builder.py` | AI build tool loop (ported from BESNIK `lumi_builder.py`), checkpoints, tripwire, push builds, promote/reject/apply |
| `teambox/jail.py` | bubblewrap + prlimit jail for every test (merged-/usr and Alpine layouts) |
| `teambox/githttp.py` | git smart HTTP (`git http-backend`) with per-member hashed tokens |
| `teambox/gateway.py` | client for BESNIK's team gateway (X-Team-Secret / X-Team-Account) |
| `teambox/tour.py` | onboarding tour, the exact bottom text, the verification prompt |
| `teambox/comms.py` + `modules/messages.py` | **Messages & calls (live)**: the notice gate, members' public keys, encrypted threads/attachments, call signalling, Lumi-only call recordings, the `/export/comms` + `/export/vault` door for BESNIK. The server stores ciphertext only. `static/comms.js` does all the crypto in the browser (WebCrypto). |
| `teambox/modules/` | **extension slots**: `feed.py` (home feed with attachments + shared copies - live), `uploads.py` (allowed types, magic check, safe serving, csv/xlsx/ods/md previews), `social.py` (comments + emoji reactions), `messages.py` (messages/calls + the owner's notice - live, see `comms.py`). See `modules/__init__.py` for the contract. |
| `static/` | UI (plain JS, phone-friendly, light/dark), `how-it-works.html` (public) |
| `starter/` | what every new project starts as (README, guardrail files, example) |
| `tests/` | `python3 -m unittest -v tests.test_teambox tests.test_feeds tests.test_comms tests.test_auth` (Linux), fake gateway + BESNIK's fake llama |
| `static/feeds.js`, `static/feeds.css` | home feed view + the Share to feed / Unshare button |

Dev run (local only): `TEAMBOX_ALLOW_DEV=1 python3 run.py --config dev-config.json` with `"dev_mode": true`, then open `/dev/login?email=you@example.com`.
