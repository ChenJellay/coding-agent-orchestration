# Prototype

The codebase itself is the working prototype. This document describes what is built, what works end-to-end, what is partially wired, and how to run it.

---

## What Is Built

### End-to-End Working (Patch Pipeline)

The complete "quick patch" pipeline runs fully today:

1. **User submits a macro_intent** via the Dashboard UI or CLI.
2. **Intent compiler** decomposes it into a DAG (LLM or deterministic demo mode).
3. **Orchestrator** runs nodes in topological order.
4. **Verification loop** per node:
   - Pre-checkpoint (file snapshot)
   - `coder_patch_v1` produces a line-level patch
   - Static checks (syntax, ruff, bandit)
   - `judge_v1` evaluates against acceptance criteria
   - On FAIL: retry with memory summarization
   - On exhaustion: `supreme_court_v1` arbitration
   - On security violation: immediate BLOCK
5. **Checkpoints** persisted with pre/post state and diff.
6. **UI reflects live progress** via polling (Kanban, node status, logs, diffs).
7. **Task Intervention** allows human guidance injection, re-run, or abort.
8. **Sign-Off view** shows diff + trace and allows merge.

### End-to-End Working (Build Pipeline — partial)

The "full TDD build" chain is wired in `chain_defaults.py` and routes correctly via `pipeline_mode="build"`. The chain steps are:
- `build_ast_context` → `context_librarian_v1` → `load_file_contents` → `sdet_v1` → `coder_builder_v1` → `write_all_files`
- `run_tests` → `load_rules` → `security_governor_v1` → `judge_evaluator_v1` → `map_evaluator_verdict`

The chain DSL is complete. The agents have correct prompts and Pydantic output models. `run_tests` can execute pytest and jest. `write_all_files` writes code and test files to disk. The main remaining step is integration testing the full TDD loop end-to-end against a live repo.

### Observability

- All events logged to `events.jsonl` with trace IDs, timestamps, and structured data.
- Live event stream via SSE (`GET /api/events/stream`).
- LLM Trace Panel in the UI (right sidebar) shows raw inference traces per step.
- Checkpoint audit trail with pre/post file content for every node.

### Agent Management

- Agent Roster page in the UI shows all 11 agents with their prompts and Pydantic schemas.
- Prompts can be edited live via the UI (PUT /api/agents/{id}/prompt) without restarting the server.

---

## How to Run

### Prerequisites
- Python 3.11+ with the backend dependencies installed (`pip install -r requirements.txt`)
- Node 18+ for the frontend
- Optionally: MLX (Apple Silicon) for local on-device inference

### Start the Dev Environment

```bash
./scripts/start-dev.sh
```

This starts the FastAPI backend (port 8001) and the Vite frontend (port 5173) concurrently.

### Configure the Target Repository

Set the repo to operate on via environment variable:
```bash
export AGENTI_HELIX_REPO_ROOT=/path/to/target/repo
```

Or use the `demo-repo/` directory included in the project for safe testing:
```bash
export AGENTI_HELIX_REPO_ROOT=./demo-repo
```

### Use Local LLM Inference (MLX, Apple Silicon)

```bash
export AGENTI_HELIX_BACKEND_TYPE=mlx_local
```

The system uses a quantized Qwen model served via MLX. Without this, the system uses the default backend (configured in `agenti_helix/__init__.py`).

### Run via CLI (No UI Required)

```bash
python -m agenti_helix.orchestration.cli run \
  --intent "Change header button color to green" \
  --repo-path ./demo-repo \
  --pipeline-mode patch
```

---

## Interaction Flow Walkthrough

The prototype reveals the intended workflow in the following sequence:

### 1. Submit a command
Open `http://localhost:5173`. In the Dashboard, type a macro_intent:
> "Update the header component to use a green button with a hover effect that darkens on hover."

Select **Quick patch** pipeline mode. Click **Submit command**. The button briefly shows "Scheduling…" and the response returns with a `dag_id`.

### 2. Watch the Kanban update
Navigate to **Features** (or wait — the Dashboard polls automatically). Within seconds the feature card appears, first in **Scoping** (intent compiling), then **Orchestrating** (nodes running).

### 3. Observe node execution
Click the feature card → **View DAG Progress**. The DAG view shows node pills color-coded by status: gray (PENDING), yellow (RUNNING), green (PASSED), red (FAILED/BLOCKED). Node pills update every 2.5 seconds.

### 4. Intervene if blocked
Click a red node → **Task Intervention**. The three-panel view shows:
- **Agent briefing**: judge's justification from the latest checkpoint
- **Execution logs**: every step with timestamps
- **Context injector**: type guidance and click **Apply + re-run**

### 5. Review the result
Once all nodes are green, the feature moves to **Ready for Review**. Click **Review & Merge** → Sign-Off view. The tri-pane shows:
- Left: original intent + acceptance criteria
- Center: semantic trace log (events)
- Right: checkpoint diff (pre/post file state)

### 6. Merge
Click **Merge to main** (calls `POST /api/tasks/merge`). The verified patch is committed.

---

## Demo Repository

`demo-repo/` at the project root contains a minimal JavaScript project with a single React component (`src/components/header.js`). It is designed to be safe to modify without risk to real code. The deterministic demo compiler (`compile_macro_intent_deterministic`) targets this file specifically and always generates a 3-node DAG for style-related intents.

Use the demo repo to validate the patch pipeline without requiring LLM compilation.

---

## Known Limitations of the Current Prototype

| Limitation | Impact | Planned Fix |
|-----------|--------|-------------|
| Build pipeline not yet end-to-end integration-tested | TDD mode may fail on first run against a real repo | Add integration test scenario (eval plan scenario 7) |
| Episodic memory uses Jaccard, not embeddings | Low recall on semantically similar but lexically different errors | Swap store backend for vector DB |
| No concurrency limit on background threads | Multiple rapid submissions can queue indefinitely | Bounded thread pool |
| Merge only works with git (no GitHub PR) | Manual step after merge | Add `gh pr create` integration |
| Static checks only cover Python (ruff/bandit) and JS syntax | No JS/TS security checks in patch pipeline | Add eslint security plugin |
| `supreme_court_v1` uses a quantized local model | Resolution quality may be lower than frontier model | Optional Anthropic/OpenAI backend |
