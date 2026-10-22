# Team box (team.xseed.ing)

The team's own computer: private per-member sandboxes (git repos), AI builds through BESNIK's capped team gateway, chat with shared
models with named settings versions, bring-your-own AI tool over git, and admin-only promote into the shared team project.
Python 3 standard library + git + bubblewrap. Install: see `INSTALL-XOS.md`.

| Path | What |
|---|---|
| `teambox/server.py` | HTTP app, routes, role checks (members: own things; admins: all; promote: admins only) |
| `teambox/identity.py` | Cloudflare Access email (only from the local cloudflared) + optional RS256 JWT verification; dev mode |
| `teambox/sandboxes.py` | sandboxes from the clean `starter/`, team-main, hardened git config |
| `teambox/builder.py` | AI build tool loop (ported from BESNIK `lumi_builder.py`), checkpoints, tripwire, push builds, promote/reject/apply |
| `teambox/jail.py` | bubblewrap + prlimit jail for every test |
| `teambox/githttp.py` | git smart HTTP (`git http-backend`) with per-member hashed tokens |
| `teambox/gateway.py` | client for BESNIK's team gateway (X-Team-Secret / X-Team-Account) |
| `teambox/tour.py` | onboarding tour, the exact bottom text, the verification prompt |
| `teambox/modules/` | **extension slots**: `feed.py` (home feed with attachments - live), `team_feed.py` (Tuesday 14:00 publish + scheduler + CLI - live), `uploads.py` (allowed types, magic check, safe serving, csv/xlsx/ods/md previews), `social.py` (comments + emoji reactions), `messages.py` (messages/calls + Marjus's notice). See `modules/__init__.py` for the contract. |
| `static/` | UI (plain JS, phone-friendly, light/dark), `how-it-works.html` (public) |
| `starter/` | what every new sandbox starts as (README, guardrail files, example) |
| `tests/` | `python3 -m unittest -v tests.test_teambox tests.test_feeds` (Linux), fake gateway + BESNIK's fake llama; `screenshots.py`, `screenshots_feeds.py` |
| `static/feeds.js`, `static/feeds.css` | home feed + team feed views |
| `systemd/teambox-feed.{service,timer}` | optional timer for the Tuesday publish (the app has its own scheduler) |

Dev run (local only): `TEAMBOX_ALLOW_DEV=1 python3 run.py --config dev-config.json` with `"dev_mode": true`, then open `/dev/login?email=you@example.com`.
