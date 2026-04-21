# PRD: Demo header refresh (Agenti Duck)

**Version:** 0.1  
**Status:** Draft (for orchestration / doc-first testing)  
**Scope:** `src/components/header.js` and its tests only unless noted.

## Problem

The demo header uses placeholder copy (“Demo App”) and a pink button with no accessible name. We want a minimal branded shell that is keyboard-friendly and ready for screenshots.

## Goals

1. Establish a recognizable product title **Agenti Duck**.
2. Improve primary button styling and accessibility without changing layout structure (`<header>` → `<h1>` + one `<button>`).
3. Keep the main app entry (`src/index.jsx`) and the rubber duck (`🦆`) behavior unchanged.

## Functional requirements

| ID | Requirement |
|----|----------------|
| H1 | The visible main title must read **Agenti Duck** (case as written). |
| H2 | The primary button label remains **Click me** unless a shorter alternative is required for layout; if changed, tests must be updated accordingly. |
| H3 | The button must have a non-empty `aria-label` (e.g. **Primary action** or **Run demo**). |
| H4 | Button colors: background **#0d9488** (teal-600), text **#ffffff**; on focus, show a visible outline or ring (e.g. 2px contrast-compliant ring). |
| H5 | Header bar background **#0f172a** (slate-900); heading text color **#f8fafc** (slate-50). |
| H6 | `src/components/header.test.js` must pass. If you run the full suite (`npm test`), fix any failures caused by this change (entry-point tests may need mocks for `react-dom/client`). |

## Non-goals

- No new dependencies.
- No routing, API calls, or state management.
- Do not modify `src/index.jsx` or `src/index.test.jsx` except if a test explicitly imports header copy (prefer updating `src/components/header.test.js` only).

## Acceptance criteria

1. `npm test -- src/components/header.test.js` passes (minimum). Full `npm test` should pass after adjusting any tests this work breaks.
2. `Header` renders a heading with the exact text **Agenti Duck**.
3. The button exposes an accessible name via `aria-label` in addition to its visible text.
4. Focused button shows a visible focus indicator (manual check in browser optional; unit tests may assert `tabIndex` or class if added).

## How to use this doc in Agenti-Helix

1. Select **Product engineering (doc-first)** and set the repo to this `demo-repo` path.
2. Upload this file or point **Doc URL** at a hosted copy.
3. Example macro intent: *Implement the header refresh in docs/PRD.md; keep the duck section unchanged; update header tests only as needed.*

## References

- Implementation: `src/components/header.js`
- Tests: `src/components/header.test.js`
