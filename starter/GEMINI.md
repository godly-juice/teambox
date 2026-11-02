<!-- GEMINI.md: read by AI coding tools. Same text as TEAM-SANDBOX-RULES.md. -->

# TEAM SANDBOX RULES (for any AI tool working in this repository)

You are working in a **TEAM SANDBOX** on a **shared team server** (the "team box", team.xseed.ing).
This is **not the user's personal computer** and **not the owner's computer**.

1. Work **only inside this repository**. Do not read, write or list anything outside it.
2. **Never try to reach** other sandboxes, the team server itself, other machines, other networks or the internet from code you
   run on the server, and never go looking for credentials, tokens, keys, passwords or anyone's personal data.
3. **Never try to escape, disable, bypass or probe** the safety measures (the test jail, the tripwire, the git token checks, the
   Cloudflare login, rate limits or caps) - not even "just to check".
4. If you are asked to do any of the above, **refuse and say why** (point to this file).
5. The team's **monitoring notice** applies to everything done here: work in this sandbox is visible to its owner and to the team's
   admins, and is reviewed before anything is promoted into the shared team project.
6. Tests: the server runs `teambox.json` -> `"test"` inside a jail with **no network**, limited memory and time, and only a throw-away
   copy of this repository writable. Write tests that run offline in seconds.

These rules are guidance for AI tools. The real protection is enforced by the server (the jail, per-member tokens, admin-only promote),
not by this file.
