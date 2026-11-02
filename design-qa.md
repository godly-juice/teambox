# Team UI design QA

Date: 2026-11-01 (America/Chicago)

## Visual truth and evidence

- Selected workspace: `C:/Users/gjoka/Documents/Codex/2026-11-01/hey-x20/outputs/ui-concepts/02-team-workspace.png`.
- User's refinement: clean, interactive spatial atmosphere inspired by igloo.inc, moving stars, retained graphite/amber workbench.
- Refined entrance: `outputs/ui-concepts/04-cinematic-entrance.png` under the same workspace root.
- Implementation: `http://127.0.0.1:4173/`, the real frontend files served by an isolated sample-data fixture. No production model or account is used.
- Desktop source and screenshot: both 1487 × 1058 pixels, CSS viewport 1487 × 1058. Comparison copies are pasted without resampling. No browser chrome or device frame.
- Final implementation screenshots: `outputs/ui-preview/15-signin-desktop-final.png`, `06-workspace-desktop-final.png`.
- Full paired inputs: `comparison-entrance.png`, `comparison-workspace.png` in that folder.
- Focused paired inputs: `comparison-entrance-form.png`, `comparison-workspace-detail.png`; these expose typography, controls, columns, row spacing, and result treatment at readable size.
- Responsive captures: `11-signin-mobile.png`, `05-workspace-mobile-v2.png`; CSS viewport 390 × 844 with no horizontal document overflow. The workspace renders Lumi first on phones and keeps DOM order consistent with the visual order.
- Additional state evidence: `07-empty-state.png`, `08-connection-error.png`, `09-gateway-refusal.png`, `10-more-menu.png`.

## Comparison history and fixes

1. Initial workspace had a completed item duplicated in the active list, queue order put queued work ahead of running work, shared-model names crowded the state column, and the extra row pushed the result section down. These were P2 information/layout mismatches. The active list now separates completed results, orders running/testing first, and uses source-aligned model/state columns. Final full and focused workspace comparisons show the corrected density and alignment.
2. Initial mobile layout placed the composer below a long work list. This was P2 for the team's assistant-first phone use. Lumi is now first at narrow widths, with keyboard-accessible jumps between Lumi and work. The mobile v2 capture confirms a visible composer in the first viewport and no horizontal overflow.
3. Entrance scene framing, form text size, and vertical placement drifted from the refined reference. These were P2 visual issues. Scene overscan was reduced, heading/field/button typography adjusted, and large-screen form/copy placement corrected. The final entrance/full form comparisons show the corrected balance. Short-height desktop rules retain accessible form controls.
4. A bright entrance scene could briefly show through the workbench during the exit fade. It is now immediately hidden outside the entrance, preserving the restrained work surface. The final workspace capture was accepted only with `data-screen=workspace` and scene visibility `hidden`.
5. Loading and mid-transition captures were rejected as evidence. The final entrance was accepted with computed scene opacity `1`; the final result summary was accepted after the real build-detail read completed.

An independent review of all four paired images and the matching router captures found no remaining actionable P0/P1/P2 issues.

## Required fidelity surfaces

- Typography: self-hosted Manrope for UI and IBM Plex Mono for metadata/code. Heading hierarchy, readable field labels, strong CTA, and 15–16px primary body text are coherent with the selected direction. Minor utility-type density versus the generated mock is P3.
- Spacing/layout: source-aligned two-column desktop proportions, flat row separators, anchored composer, restrained radii, wide readable entrance. Phone layout is an intentional responsive interpretation of the desktop concept.
- Colors/tokens: graphite surfaces, off-white text, muted secondary text, warm amber action/focus, explicit semantic status colors. Light appearance remains available; the cinematic entrance retains its legible dark palette.
- Assets: an original generated planet/space backdrop is installed as a real raster asset. The starfield is an actual local animation with pointer response. Phosphor icons are supplied SVG files, not handcrafted approximations. Font/icon licenses are included. No cartoon mascot, fake UI texture, or baked-in form controls.
- Copy/content: actual API model/status/result semantics take precedence over fictional mock claims. The Model column replaces the mock Owner field; background work is explicitly unavailable until connected; the preview is labeled; saved welcome history differs from the mock's two-message conversation. The original PIN/invite/privacy requirements remain. No fake audio toggle is shown.

## Interaction and implementation checks

- Preview sign-in, wrong PIN recovery, sign-out, project navigation, Messages destination, and More disclosure checked in the in-app browser.
- Lumi send/reply checked using the isolated fixture; no model called. Empty Lumi, disconnected resource, and HTTP-200 refusal states remain visible and truthful.
- Final source review verified fresh-only positive model availability, generation guards for late chat reads, account/route disposal, and duplicate-send prevention.
- Latest-result refresh race was fixed: a delayed detail reply updates the current matching excerpt even if another source repainted the panel.
- Motion pause/resume checked; phone More menu exposes motion and light/dark actions. Escape closes More. System reduced-motion and hidden-tab suspension are implemented; OS-level preference mutation was not performed.
- Phone light/dark switching checked and restored to dark. Keyboard focus is visible; the PIN error returns focus to PIN.
- JavaScript syntax, adapter failure/account/refusal checks, and `git diff --check` passed. No browser console errors/warnings were returned in the accepted preview run.

## Limits and follow-up

This passes frontend design/preview QA, not production integration acceptance. Real sessions/invites, gateway credentials, background scheduling/preemption, production CSP/MIME, encrypted messages/calls, vault, and device deployment remain in Claude's integration checklist. Fixture timestamps and sample wording are not production activity. Full accessibility compliance is not claimed.

P3 follow-up: optional utility-type refinements after real team data is connected. Do not recreate fictional progress percentages or availability to match the mock.

final result: passed
