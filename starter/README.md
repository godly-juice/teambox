# Your team sandbox

> **TEAM SANDBOX** - this is the team's shared workspace on the team computer. Not your computer, not Marjus's computer.

This repository is one of your private sandboxes on the team box (team.xseed.ing). Only you and the team's admins can see it.
It started from a clean template - it contains no one's personal files and no code from anyone's own computer.

## What is here

| File | What it is |
|---|---|
| `hello.py`, `test_hello.py` | A tiny example with a test. Replace them with your own work. |
| `teambox.json` | The test command the team box runs (in its jail) after every push and every AI build. |
| `TEAM-SANDBOX-RULES.md` | The rules for AI tools working here. |
| `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.cursor/rules/`, `.github/copilot-instructions.md`, `opencode.json` | The same rules, in the file each AI tool reads (Claude Code, Codex / OpenCode, Gemini, Cursor, Copilot, OpenCode). |

## Using your own AI tool (Claude Code, Codex, Cursor, OpenCode, ...)

1. On the team box, open **Profile -> Git tokens** and make a token. It is shown **once** - copy it.
2. Clone this sandbox (the exact address is on the sandbox page):
   `git clone https://team.xseed.ing/git/<your-id>/<sandbox>.git`
   Username: anything (your email is fine). Password: the token.
3. Open the folder in your AI tool and work as usual. When ready: `git push`.
4. The team box runs the tests in its jail and shows the result on the **Builds** page. An admin can then review and promote it.

## Important: the guardrail files are guidance, not the protection

`CLAUDE.md`, `AGENTS.md`, `.cursorrules` and the others tell AI tools to stay inside this repository and not to probe or escape
anything. A tool can ignore them. **The real protection is on the server**: every test runs in a jail (bubblewrap) with no network,
limited memory and time and only a throw-away copy of this sandbox writable; a token reaches only its owner's sandboxes; only admins can
promote work into the shared team project. You may edit these files (it is your sandbox) - the server's protection does not depend on them.

## Monitoring

The team's monitoring notice applies: work in this sandbox is visible to you and to the admins and is reviewed before anything is
promoted. See the site's "How it works" page for exactly what is stored and who can see it.
