# Frontend Inspection — Action Items

Ordered by severity. Page references include line numbers in `frontend/src/App.tsx`.

---

## 🔴 Critical — Duplicates that confuse action

### 1. Two "Run" buttons for the same action (Dashboard, L314 + L361)
`DashboardPage` renders two buttons that both call `handleRunCommand()`:
- **"Run command"** — top-right, next to the repo input field (L314–331)
- **"Submit command"** — below the textarea (L361–381)

Both show `"Scheduling…"` while busy. The user is expected to type in the textarea first, then click — making the top "Run command" unreachable without scrolling up. They're also styled differently (one is unstyled, the other has `var(--primary)` border/background), implying different actions.

**Fix:** Remove "Run command" (L314–331). Keep only "Submit command" below the textarea where the user already is. The `⌘/Ctrl+Enter` hint (L380) is sufficient for keyboard users.

---

### 2. Two links to the same page on every FeatureCard (FeatureCard, L1019–1050)
Each card renders three link pills at the bottom:
- **"View DAG Progress"** → `/features/{id}` (correct)
- **"View Trace Logs"** → `/features/{id}/signoff`
- **"Review & Merge"** → `/features/{id}/signoff`

"View Trace Logs" and "Review & Merge" are the same URL. The card already navigates to the DAG page on click.

**Fix:** Keep "View DAG Progress" and one combined "Review & Merge" pill. Remove "View Trace Logs".

---

### 3. "Attach doc link" and "Apply + re-run" without guidance for when to use each (TaskInterventionPage, L1585–1607)
The context injector panel has two sequential actions on the same inputs:
- **"Attach doc link"** — saves doc URL only (no rerun)
- **"Apply + re-run"** — applies guidance + doc URL + reruns

"Attach doc link" is only useful as a prerequisite step if the user doesn't want to rerun yet, but no hint communicates this. Most users will click "Apply + re-run" which does both anyway.

**Fix:** Remove "Attach doc link". `applyAndRerun` already accepts `doc_url`, so both inputs are consumed in one step. If standalone context injection is needed in the future, add it as a separate settings screen.

---

## 🟡 Redundant display elements (same data shown twice)

### 4. "Column mix" pills duplicate the stat cards (Dashboard, L589–606)
The five stat cards at L490–518 already show: Trust score, In-flight DAGs, Blocked, Verifying, Ready for Review — with counts and descriptions.

The "Column mix" section below (L589–606) shows the exact same five columns as small pills: `SCOPING: 0 ORCHESTRATING: 0 BLOCKED: 0 VERIFYING: 0 READY_FOR_REVIEW: 0`.

**Fix:** Remove the "Column mix" section entirely.

---

### 5. "What will happen" panel re-explains pipeline radio button descriptions (Dashboard, L442–467)
The pipeline radio options (L388–438) already include:
- A label (`"Quick patch"`)
- A monospace agent chain (`coder_patch_v1 → judge_v1`)
- A description sentence

The "What will happen" panel (L442–467) re-states the same information in numbered prose, switching on `pipelineMode`. It adds no new information.

**Fix:** Remove the "What will happen" panel. Expand the description field in each radio option slightly if more context is needed.

---

### 6. Rules.json shown on both Repository Context and Settings pages
- **RepositoryContextPage** (L823–853): "Rules" tab → disabled textarea with rules.json
- **SettingsPage** (L933–966): "Governance rules" section → disabled textarea with rules.json

Two pages, two identical read-only views of the same file.

**Fix:** Show rules.json only on Repository Context (where it belongs alongside the repo map). Remove the governance rules textarea from Settings — keep just the API connection info and the `keys: N` summary line with a link to `/repo#rules`.

---

### 7. ComputePage is a single-stat page already visible on Dashboard (L2253–2285)
`ComputePage` renders one number: `events.jsonl count: {N}`. The Dashboard already shows this same number inline: `"Compute burn proxy: {N} events"` (L524).

The nav item takes up a slot for a page that adds nothing beyond what's already on the first screen.

**Fix:** Remove the `/compute` route and its sidebar nav link. The Dashboard inline display is sufficient.

---

## 🟠 Interface noise / placeholder clutter

### 8. Topbar "Burn: —" and "Profile" are unpopulated placeholders (L2443–2444)
Both pills are static strings:
```tsx
<span className="pill">Burn: —</span>
<span className="pill">Profile</span>
```
Neither is wired to data or an action. They've been there since the scaffold.

**Fix:** Remove both. Add them back when wired (burn rate = real compute metric; profile = auth context).

---

### 9. Placeholder component has stale scaffold copy (L71–88)
The catch-all 404 route uses `<Placeholder>` which renders:
> "UI scaffold is live. Next: wire to the local API and implement the Kanban/DAG/tri-pane flows."

The app is fully wired. This text hasn't been updated.

**Fix:** Replace with a simple "Page not found" message and a link back to `/`.

---

### 10. Two line-limit controls for repo map that overlap in purpose (RepositoryContextPage, L755–797)
The repo map viewer has both:
- A **dropdown** for max lines (200 / 500 / 800 / 2000 / 5000)
- A **"Show all" toggle button** that overrides the dropdown

The dropdown is redundant when "Show all" exists. If you can show all, why preset limits?

**Fix:** Remove the dropdown. Keep the "Show all / Show truncated" toggle with a sensible default cap (e.g., 1000 lines). Or keep the dropdown and remove the toggle, replacing it with an `All` option in the dropdown.

---

## 🔵 UX / consistency issues

### 11. "Merge to main" is same visual style as navigation pills (SignoffTripanePage, L1768–1778)
The header row of Sign-Off contains four same-styled `className="pill"` links:
`Back to DAG` · `Edit intent` · `View episodic memory` · **`Merge to main`**

"Merge to main" is a destructive irreversible action. It blends in with the navigation pills, increasing risk of accidental clicks.

**Fix:** Style "Merge to main" as a distinct danger button (e.g., red border + red text, or a `<button>` with `background: var(--error-bg)`).

---

### 12. `ErrorBox` component exists but is only used on Dashboard; other pages reinvent it inline
`ErrorBox` (L90–108) is a reusable styled error block. But `FeatureDagPage` (L1212), `TaskInterventionPage` (L1501), `SignoffTripanePage` (L1782), `TriageInboxPage` (L1987), and `FeaturesKanbanPage` (L2338) all copy-paste inline divs with the same pattern.

**Fix:** Replace all inline error divs with `<ErrorBox>`. This is a pure refactor with no behavior change, but makes the code consistent and simplifies future styling.

---

## Summary table

| # | Page | Issue | Action |
|---|------|-------|--------|
| 1 | Dashboard | Two submit buttons, same action | Remove "Run command" (L314–331) |
| 2 | FeatureCard | Two links to the same URL | Remove "View Trace Logs" pill |
| 3 | Task Intervention | Two context inject actions, one redundant | Remove "Attach doc link" |
| 4 | Dashboard | "Column mix" = stat cards repeated | Remove "Column mix" section |
| 5 | Dashboard | "What will happen" = radio desc repeated | Remove "What will happen" panel |
| 6 | Settings | Rules.json shown again (also on /repo) | Remove from Settings page |
| 7 | Compute | Entire page = one number on Dashboard | Remove page + nav link |
| 8 | Topbar | Static placeholder pills | Remove "Burn: —" and "Profile" |
| 9 | 404 Route | Stale scaffold copy | Update Placeholder copy |
| 10 | Repo Context | Dropdown + toggle button overlap | Pick one line-limit control |
| 11 | Sign-Off | Destructive "Merge" styled as nav pill | Style as danger button |
| 12 | All pages | ErrorBox unused; inline copies exist | Replace inline error divs with ErrorBox |
