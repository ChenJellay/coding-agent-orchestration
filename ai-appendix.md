# AI Appendix — Claude & Cursor on Agenti-Helix

This document summarizes how **Anthropic Claude** and **Cursor** were used effectively to scaffold, architect, implement, and debug this repository. It is written for contributors who want to repeat the same workflow or understand where human judgment still matters.

---

## 1. What this repo is (context for any assistant)

**Agenti-Helix** is a local-first control plane for an AI-native SDLC: intent compilation, DAG orchestration, verification (checkpointing + judge), and a React dashboard. Major surfaces:

| Area | Path | Notes |
|------|------|--------|
| Control-plane API | `backend/agenti_helix/` | FastAPI, orchestration, agents, verification |
| Agent prompts | `backend/agenti_helix/agents/prompts/` | Markdown system prompts per role |
| Web UI | `frontend/` | Vite + React/TypeScript |
| Demo target repo | `demo-repo/` | Default `AGENTI_HELIX_REPO_ROOT` for runs |
| Local stack | `scripts/start-dev.sh` | Judge (8000) + API (8001) + Vite (5173) |
| Product spec / phases | `README.md` | Phased roadmap and acceptance-style goals |

When you point an AI at the project, **attach or `@`-mention** `README.md` and the specific package (`backend/...` or `frontend/...`) you are changing so answers stay grounded in this layout.

---

## 2. Cursor — primary in-repo driver

Cursor is the best fit for **tight loops inside the codebase**: search, edit, run commands, and fix linter/type errors without leaving the editor.

### Scaffolding

- **Composer / Agent mode**: Use for multi-file features (new API route + types + a thin UI call) with an explicit checklist in the prompt (“add endpoint X, wire `frontend/src/lib/api.ts`, no unrelated refactors”).
- **Chat + `@` symbols**: Pin `README.md`, `scripts/start-dev.sh`, and the file you are editing so the model inherits ports, env vars, and naming conventions.
- **Rules (optional)**: This repository gitignores `.cursor/`; if you use project rules or `AGENTS.md` locally, keep them **short and actionable** (stack versions, “do not edit `node_modules`”, API base URL patterns).

### Architecture & design alignment

- Treat **`README.md` phases** as the source of truth for scope: when designing a change, ask the assistant to map it to “Phase N” so you avoid building Layer 4 UI details before Layer 2 invariants exist.
- Use **`deliverables/`** and any internal design notes as **read-only context** for larger refactors; paste summaries into the prompt instead of expecting the model to discover every doc.

### Building & running

- Prefer **`./scripts/start-dev.sh`** (optionally `--repo /path/to/repo`) over ad-hoc terminals so judge/API/UI env stay consistent.
- When the model suggests commands, have it run **from repo root** and respect `backend/.env` / `frontend/.env.local` as documented in `README.md`.

### Debugging

- **Backend**: Trace orchestration and tools through `backend/agenti_helix/observability/` and API logs from the uvicorn process started by the script.
- **Runs against a repo**: Under the target repo, inspect `.agenti_helix/logs/` (e.g. `events.jsonl`) for emitted events during a task.
- **Frontend**: Use the dashboard’s trace/diff panels (see `README.md` “tri-pane” / prototype sections) together with browser devtools network calls to `VITE_API_BASE_URL`.
- Give the assistant **one failing symptom + one log snippet** (trace ID, HTTP status, or last 20 lines) before asking for a root-cause hypothesis.

### Cursor habits that pay off here

1. **Small prompts, small diffs** — match the project’s own agent design (minimal patches, verification loops).
2. **Name the layer** — “this is orchestration only, no UI” prevents scope creep.
3. **Verify after edits** — `pip`/`npm` checks or a quick `curl` to the API beats long speculative explanations.

---

## 3. Claude — planning, prose-heavy design, and cross-cutting review

Use **Claude** (web app, API, or **Claude Code** in a terminal) when you want **longer reasoning or document-first iteration** without thrashing the file tree.

### Scaffolding & architecture

- Paste the **relevant section of `README.md`** (e.g. Phase 2 verification) and ask for: component list, data flows, failure modes, and test plan **before** implementation.
- Ask for **API contracts and types** as markdown tables; then paste those into Cursor to implement — reduces mismatch between “what we agreed” and “what was coded.”
- For **prompt engineering** (`agents/prompts/*.md`), Claude is often effective at drafting strict judge/coder rubrics; Cursor is effective at wiring those prompts into `agents/models.py` / runtime code and validating behavior end-to-end.

### Building & integration

- Use Claude for **dependency or licensing questions**, migration narratives (e.g. Python or Node major bumps), and “what breaks if we change X” when X spans backend + frontend + demo-repo.
- Keep **secrets out of the chat**; reference env *names* only (`OPENAI_API_KEY`, `AGENTI_HELIX_REPO_ROOT`, etc.).

### Debugging & postmortems

- Feed Claude structured artifacts: **redacted** stack traces, a single request/response shape, or a minimal repro description. Ask for a **ranked list of hypotheses** and **concrete checks** (not a rewrite of the whole subsystem).
- For judge/orchestrator disagreements, paste **intent + acceptance criteria + judge output**; ask whether the prompt, the tool output, or the state machine is wrong.

### Claude Code / parallel worktrees (optional)

Some workflows use **`.claude/worktrees/`** (or similar) for isolated experiments. If you use that pattern:

- Merge back through normal **git review**; treat AI-generated trees like any feature branch.
- Avoid duplicating “source of truth” — one canonical `backend/agenti_helix/` tree in version control.

---

## 4. Suggested division of labor

| Task type | Prefer |
|-----------|--------|
| Implement FastAPI route + Pydantic models | Cursor |
| Refactor React component with immediate visual check | Cursor |
| Draft phase-wide architecture or ADR-style writeup | Claude |
| Author or tighten long system prompts | Claude draft → Cursor integrate |
| Run stack, fix port/env issues, grep codebase | Cursor |
| Security review of a proposed design (threat assumptions) | Claude + human |

---

## 5. Pitfalls specific to AI-assisted work on this repo

1. **Wrong repo root** — Many behaviors depend on `AGENTI_HELIX_REPO_ROOT`. The AI may assume the monorepo root; clarify when the target is `demo-repo/` or another workspace.
2. **Two languages, two toolchains** — Backend Python and frontend TypeScript need **separate** lint/test commands; ask explicitly for both when the change is full-stack.
3. **Large generated dirs** — Do not commit `frontend/node_modules/` or point bulk edits at build artifacts.
4. **Prompt vs code drift** — After editing `agents/prompts/*.md`, grep for the prompt slug in Python so runtime still loads the intended file.
5. **Over-automation** — The product is about **verified** edits; resist “rewrite the orchestrator” unless verification and checkpoints are part of the same task.

---

## 6. Minimal prompt template (copy/paste)

```text
Repo: Agenti-Helix (see README.md).
Task: <one sentence>.
Constraints: Touch only <paths or layers>. No unrelated refactors.
Acceptance: <how you will verify — script, curl, UI step>.
Environment: ./scripts/start-dev.sh, demo-repo unless specified.
```

---

## 7. Maintenance of this appendix

Update this file when the **canonical dev entrypoints** or **layer boundaries** change (for example, if the API package layout or default ports change). Keep it factual and short so both humans and models can load it quickly at the start of a session.
