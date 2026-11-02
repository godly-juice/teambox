# Team workspace UI integration handoff

This branch replaces the Team box entrance and default Home view with the selected **Operator Desk (option 2)** direction, a Lumi side conversation, and a restrained cosmic entrance. The new workspace displays existing builds and model data. It does not implement a scheduler or connect new infrastructure.

## Checkout and scope

- Repository: `D:\Atlas\teambox`
- Isolated worktree: `C:\Users\gjoka\Documents\Codex\2026-11-01\hey-x20\work\ui-redesign\teambox-ui`
- Branch: `codex/team-ui-operator-desk`
- Base: clean `main`, commit `12d3662`
- Production address: `https://team.xseed.ing/`
- Original production CSS/JS were verified against the base checkout: `/static/app.css`, `/static/app.js`, and `/static/feeds.css` matched exactly before this work.
- This handoff accompanies the reviewed frontend. It has not been sent to another person or agent; production connection and deployment are the next integration steps.

Review the final branch diff/status before integrating. Frontend design QA passed locally; see design-qa.md. Do not overwrite unrelated worktrees or production data.

## Files

| File | Purpose |
| --- | --- |
| `static/index.html` | Same shell IDs, new branding, same-origin asset loading, decorative scene and explicit motion control |
| `static/app.js` | Updated sign-in presentation/navigation; delegates Home to `TB.renderWorkspace`; view teardown hook |
| `static/operator.css` | Shared visual tokens, entrance, desktop workspace, phone layout, focus treatment, reduced-motion rules |
| `static/workspace.js` | Workspace rendering, real build rows, latest result, saved Lumi conversation, foreground-only refresh |
| `static/workspace-data.js` | Existing API adapter; per-source loading/error/stale states; account isolation; typed Lumi send results |
| `static/universe.js` | Local canvas starfield, pointer response, pause preference, hidden-tab suspension and reduced-motion handling |
| `static/assets/team-universe.png` | Entrance backdrop |
| `static/assets/fonts/` | Self-hosted Manrope and IBM Plex Mono, including OFL licenses |
| `static/assets/icons/` | Self-hosted SVG icons and license |

Application server, identity, auth, account roles, invites, PIN checks, crypto, vault, communications, model gateway, build execution, git and promotion behavior are unchanged. `static/comms.js`, `static/vault.js`, and `static/feeds.js` retain their original behavior. UI elements continue using the safe `TB.h`/`textContent` builder rather than member/model HTML.

## Existing API contracts

`TB.api` retains `X-Teambox: 1`, same-origin credentials, and the existing server role checks.

| Request | Response/use |
| --- | --- |
| `GET /api/me` | Account identity; `id`, `admin`, `display_name`, `welcome_chat`, `first_project`, `setup_next`, `profile.timezone` |
| `GET /api/modules` | Existing module navigation/extension slots |
| `POST /api/auth/login` | Original `{identifier,pin}` authentication flow |
| `GET /api/builds` | `{builds,running,max_total}`; member rows are private; `running` is the global build count |
| `GET /api/builds/<id>` | A **top-level build object**, including `summary`, `last`, `task`, `status`, `verdict`, review material and server-authorized actions |
| `GET /api/sandboxes` | `{sandboxes}` with `owner,name,label,created,busy`; existing `/api/sandboxes/...` project flows remain available |
| `GET /api/models` | Actual visible shared models, notices, caps and team model/RAM totals; an HTTP 200 response can still contain `ok:false,refused,status` |
| `GET /api/chats/<welcome_chat>` | Member's existing Lumi welcome conversation; no automatic welcome/setup POST |
| `POST /api/chats/<id>/send` | `{text}`; reply can have HTTP 200 **and** `refused`/upstream `status`. Show its saved notice and do not claim success. |

Build list fields: `id,kind,owner,owner_email,sandbox,title,status,verdict,created,updated,model,steps,files_changed,last`. There is no build percentage, ETA, GPU utilization or generic worker-health figure. Keep unknown values unknown.

Model fields include `id,name,state,startable,ram_gb,started_by_team,started_by_you`; team summary includes `models_running,ram_gb,max_models,ram_gb_cap`. Model/RAM figures must retain those meanings rather than being relabeled as worker activity.

`TB.workspaceData.load()` returns independent `builds`, `sandboxes`, and `models` resources, each with `status,data,fetchedAt,attemptedAt,error,errorStatus,stale`. Cached data must be visibly stale when refresh fails. Only a fresh successful model source can establish positive availability. `subscribe(fn)` supplies incremental updates; `getState()` returns a snapshot. `getLumiChat()` and `sendLumi(text)` return typed results (`ready`, `empty`, `unavailable`, `error`, `refused`) and sanitized chat data.

The full chat has `lumi:true` and may contain fallback messages. A chat with model `"(no model)"` is unavailable. The chat-list summary does not contain `lumi`; use the member's `welcome_chat` ID instead of guessing from a title. A normal model conversation must not be relabeled as Lumi. Text sent to the chat endpoint currently does **not** enqueue a build by itself; real build creation remains in the Projects flow.

## Connect the real background queue

The adapter currently reports **`backgroundQueueConnected: false`**. The workspace states that it shows AI builds and that background work is not available yet. No synthetic workers, assignments, availability, priority claims or progress are presented as operational facts.

Claude owns the technical connection. Coordinate an authenticated, server-authorized queue provider with this frontend rather than reading machine files or credentials from the browser. The provider should supply actual queue items, foreground/background kind, priority, runner availability, timestamps, freshness/disconnection state and the scheduler's real policy. Agree on the contract before adding a new endpoint. An optional provider seam is not implemented yet; the default is existing Team box builds only.

Required semantics:

1. Real user/team requests outrank optional background work **in the scheduler**, not only in display order. Respect existing owner/active-use protections and safe boundaries for pausing work.
2. Mark the background connection active only after the authorized source answers successfully. A refused, empty, stale or disconnected source must not become a positive availability claim.
3. Keep member visibility and admin visibility enforced by the server; do not expose others' private projects, task text, prompts, tokens or logs through a combined queue.
4. Expose only safe display data needed for the decision: item ID/title, accessible project, kind, actual status/priority, relevant runner label, last update and plain failure reason. Keep model and account caps intact.
5. Publish timestamps with an explicit UTC offset/`Z` and document display-zone behavior. The current backend's `store.now_s()` uses naive server-local strings; interpreting these as UTC without a contract can show incorrect times. The member's preferred zone is `me.profile.timezone`.
6. Preserve the read/send distinction. Displaying the workspace must not create chats, run setup, start models, enqueue tasks or trigger inference. A future natural-language assignment needs an explicit supported action and honest result.

## Preview and verification

Local preview: **`http://127.0.0.1:4173/`**. Server source and protocol are outside the git worktree:

- `work/ui-redesign/preview_server.py`
- `work/ui-redesign/PREVIEW-PROTOCOL.md`

Sample credentials: **jordan / 123456**. These are fixture-only values, unrelated to real accounts. `/api/ui-preview` is the same-origin proof used to show the **Preview · sample data** label and credential hint. Production does not implement it. Never retain a fixture hint without that positive probe.

The preview reads current worktree static files. Every API result and chat reply is a local in-memory fixture. It never imports the production backend, calls an AI model, runs tests/builds, or connects production infrastructure. `?state=normal|empty|error|refused` tests independent browser-scoped states. `/api/ui-preview/state` is a fixture control, not a production route. Do not deploy this preview server or fixture API.

Completed local checks include adapter account isolation, partial failures, malformed replies, stale data, fallback messages and HTTP-200 refusals. Preview protocol checks covered auth, static MIME, sample builds/projects/detail, deterministic local chat, refusal/empty/error states, unknown endpoints and path containment. The main task separately checks desktop/phone browser flows.

Before publishing `team.xseed.ing`:

- Re-run real session sign-in, invalid credentials, sign-out, session expiry, invite redemption and required setup; confirm existing account controls remain effective.
- Verify workspace rows and full-result summary against actual API responses. Test the empty state, partial disconnect, gateway refusal, stale refresh, and recovery without claiming positive availability from cached data.
- Verify the actual member's Lumi chat, fallback/unavailable model, failed send, double-submit prevention and saved history. Force a delayed refresh response after a successful send; it must not replace the newer reply. Check route disposal/sign-out during requests.
- Test Projects, AI builds, Models, feed, Messages/calls, Vault and admin links with suitable real test roles. Do not modify encryption, monitoring notices or server authorization as part of visual integration.
- Check keyboard/focus order, visible focus, PIN reveal labels, narrow-screen Lumi-first order, mobile navigation, horizontal overflow and both light/dark presentation.
- Verify reduced-motion, explicit pause control on every screen that animates, hidden-tab suspension and no scene movement under the reduce preference.
- Retain the existing CSP: same-origin scripts/styles/fonts/assets, no remote font dependency, no inline-script exception. Confirm font/image/icon loading under the real headers. The native static MIME table currently has no explicit `.ttf` entry; review correct font MIME in technical integration without weakening CSP or `nosniff`.
- Deploy reviewed static files/assets through the existing technical workflow; do not replace the server, config or data directories with the preview infrastructure.

## Review resolution

All three initial findings are resolved in the current frontend source:

- Positive model availability requires a fresh `ready` model resource, and a separate live status note exposes model-source errors.
- Chat refresh captures `chatGeneration`; submission invalidates older reads, which cannot replace the newer send result. Route/account guards continue to reject updates after navigation or sign-out.
- The More menu exposes a keyboard-accessible background-motion toggle on phones even when the decorative scene's separate control is hidden.

These changes were re-reviewed in source. Retain the delayed-response, gateway-refusal and phone-motion checks in final browser acceptance; production connections remain Claude's integration work.
