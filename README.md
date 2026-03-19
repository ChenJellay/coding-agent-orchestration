### Agenti-Helix Implementation Plan

**Status**: Core primitives in place; refactored into frontend/backend modules  
**Goal**: Implement Agenti-Helix as an AI-native SDLC Control Plane, progressively layering reliability, orchestration, UI, and enterprise hardening.

---

## 1. Overview

Agenti-Helix is a four-layer system:

- **Layer 1 – Intent & Context Engine (Helix Core)**: Requirements ingestion, DAG task compilation, Repository Map, AST-aware RAG.
- **Layer 2 – Agent Orchestration Hub (Agenti Router)**: Checkpointed execution loops, pluggable agents, long-term memory.
- **Layer 3 – Observability & Verification Fabric (Control Plane)**: Semantic tracing, autonomous verification loops, auto-correction routing.
- **Layer 4 – Human Control Interface (Monitorer’s Cockpit)**: Trust dashboard, DAG visualization, tri-pane diff/intent/reasoning review.

The 5 phases below build these layers incrementally, starting from a single reliable file edit to a full enterprise-ready platform.

---

## 2. Core – Single-file edit primitive ✅

**Objective**: One agent reliably edits one file without hallucinating, using AST-aware RAG and a Repository Map.

**Core Components**

- **Repository Map Generator**
  - Walk the repo and generate a compressed index:
    - File paths
    - Class definitions
    - Function signatures
    - Import graph at a high level
  - Output as JSON/JSONL (e.g. `repo_map.json`).

- **AST Parser & Chunker**
  - Per-language adapters (start with JS/TS + Python):
    - Parse files into an AST.
    - Produce chunks that are **full functions/classes only** (never arbitrary token windows).
    - Track imports and call graph edges (basic dependency graph).

- **RAG Retrieval Layer**
  - Given an intent (e.g. “Change the button color in `header.js`”):
    - Use the Repository Map to shortlist candidate files.
    - Fetch AST chunks for those files.
    - Retrieve dependent signatures via the dependency graph (functions/classes that are called/used).

- **Single-Agent Executor**
  - System prompt that:
    - Receives: intent, repo map slice, retrieved AST chunks, and file content.
    - Returns: a minimal, well-scoped diff (patch) for one file.
  - Apply the diff to the working tree and validate syntax (language-appropriate basic checks).

**Testing**

- Construct tasks like “Change the button color in `header.js`”:
  - Verify:
    - Correct file selection via the Repository Map.
    - Diff is syntactically valid and localized (no collateral damage).
    - No hallucinated symbols/paths.

---

## 3. Verification – Checkpointing & Local Judges

**Objective**: Wrap the single-agent loop in a checkpointed, self-verifying state machine using local judge models.

**Core Components**

- **Checkpointing System (e.g. LangGraph-based)**
  - For each “node” (single edit task):
    - Capture pre-state: repo snapshot reference (commit or working-tree hash), target file, retrieved context.
    - Capture post-state: diff, updated file content, any tool logs (lint/test).
  - Implement rollback:
    - If verification fails, revert to the last known-good checkpoint.

- **Local Judge Model Integration**
  - Spin up a **local quantized 8B model** (e.g. Qwen Coder 8B 4/8-bit) behind a thin API.
  "uvicorn phase2.judge_server:app --host 127.0.0.1 --port 8000"
  - Define a strict system prompt:
    - Role: **binary judge**, not coder.
    - Inputs: acceptance criteria, original snippet, edited snippet, language, and any test/lint logs.
    - Output: `PASS/FAIL` + short justification + list of problematic lines if FAIL.
  - Route **all verification** to this local judge to align with cost-aware routing principles.

- **Verification Loop**
  - Execution pipeline for a single task:
    1. Take checkpoint (pre).
    2. Run coder agent (Phase 1 pipeline).
    3. Run fast static checks (lint/format, lightweight tests if available).
    4. Call the Judge model with strict criteria.
    5. If FAIL:
       - Do not advance global state.
       - Generate a corrective prompt summarizing the judge’s findings.
       - Retry the coder agent up to N times.
    6. If still failing after N retries:
       - Mark the node as blocked and prepare an escalation payload (for future human or “Supreme Court” routing).

**Testing**

- Intentionally mis-prompt the coder agent (e.g. ask it to delete logic or break types).
- Ensure:
  - The Judge flags the bad code reliably.
  - The checkpoint remains stable (can roll back).
  - The retry loop uses judge feedback to attempt fixes.
  - State halts on persistent failure (no silent corruption).

---

## 4. Orchestration – DAG Routing & Task Breakdown

**Objective**: Turn the reliable single-task loop into a DAG-based orchestrator that sequences multiple tasks deterministically.

**Core Components**

- **Intent Engine (Layer 1 DAG Compiler)**
  - Input: High-level feature or PRD snippet (macro-intent).
  - Output: **3–5 node DAG** of micro-tasks, each with:
    - Description (e.g. “Update header button color”, “Update tests for header button color”).
    - Target file/type hints (from the Repository Map).
    - Acceptance criteria (for the Judge).
  - Incorporate repository constraints and global rules (from later phases) into task specs where applicable.

- **DAG Execution Engine**
  - Represent nodes and edges with explicit states:
    - `PENDING → RUNNING → PASSED_VERIFICATION → FAILED → ESCALATED`.
  - Execution semantics:
    - A node triggers **only when all predecessors are `PASSED_VERIFICATION`**.
    - Each node uses the Phase 2 loop:
      - Coder execution → local Judge → checkpointing and retries.
  - Include:
    - Hard limits on retries per node.
    - Deterministic ordering when multiple children are ready.

- **State Persistence & Observability Hooks**
  - Persist DAGs, node states, and checkpoints to a durable store.
  - Emit events for:
    - Node start/finish.
    - Judge verdicts.
    - Rollbacks and escalations.
  - These events will later drive the UI (Phase 4) and auditability (semantic tracing).

**Testing**

- Use a macro-feature like:
  - “Update header button color, refactor shared button styles, and update tests.”
- Validate:
  - DAG structure (3–5 nodes) is sensible and ordered (e.g., style refactor before tests).
  - Task A must pass the Judge before Task B can run.
  - Failures in an early node prevent downstream node execution and are visible in state.

---

## 5. Interface – The Helix Canvas

**Objective**: Build the human-facing “Helix Canvas” UI that visualizes DAGs, state, and semantic traces, and supports management-by-exception.

### Interface (prototype) – How to run locally

This repo includes an early UI prototype under `frontend/` and a control-plane API under `backend/`:

- **API server (FastAPI)**: serves `.agenti_helix/` artifacts + derived views (`/api/features`, `/api/triage`, etc.)
- **Web UI (Vite + React/TypeScript)**: Notion-like shell with Features Kanban, Feature DAG view, Triage Inbox, Task Intervention, and Sign-Off tri-pane (v1)

Run the API:

```bash
uvicorn agenti_helix.api.main:app --reload --port 8001
```

Run the web app:

```bash
cd frontend
npm install
npm run dev
```

If you run the API on a different host/port, set:

```bash
export VITE_API_BASE_URL="http://127.0.0.1:8001"
```

**Core Components**

- **Kanban Board / DAG Visualization**
  - Columns mapped to node states (e.g. `Backlog`, `In Progress`, `Verifying`, `Passed`, `Failed`, `Escalated`).
  - Each card represents a DAG node:
    - Title (micro-task intent).
    - Status.
    - Linked DAG relations (predecessors/successors).
  - Syncs with orchestration state via a backend API.

- **Context Injector Modal**
  - Allow humans to:
    - Attach additional constraints (e.g. security rules, design guidelines).
    - Modify acceptance criteria or global rules for a DAG run.
  - Inject this context into:
    - Intent Engine (for future task compilation).
    - Judge prompts (as additional evaluation criteria).

- **Tri-Pane Review Cockpit**
  - Layout:
    - Left: **Original intent / user story / PRD excerpt**.
    - Center: **Semantic reasoning trace**:
      - RAG chunks retrieved.
      - Tools used.
      - Judge verdicts and retry history.
    - Right: **Code diff** (before vs after), linked to trace IDs.
  - Connect to semantic tracing:
    - Clicking a diff line shows the reasoning step and associated RAG chunks (towards “Semantic Git Blame”).

**Testing**

- Run a simple DAG (e.g. header color change with tests).
- Verify:
  - All node state transitions are visible in the Kanban/DAG view.
  - Context added via the modal changes Judge behavior (e.g. stricter criteria).
  - Tri-pane review shows coherent mappings between intent, reasoning, and diff.

---

## 6. Phase 5 – Enterprise Moat (Sandboxing & Optimization)

**Objective**: Harden the system for real-world, production-grade use with sandboxed execution, cost-awareness, and global policy enforcement.

**Core Components**

- **Ephemeral Docker Sandboxing**
  - For verification nodes:
    - Spin up a temporary container per task (or small batch):
      - Mount working copy.
      - Install dependencies (cached where possible).
      - Run compilation, tests, and static analysis tools.
    - Collect logs:
      - Test failures.
      - Runtime errors.
      - Security scan results.
    - Tear down containers regardless of success/failure.
  - Feed logs into:
    - Judge model input.
    - Auto-correction routing (Phase 3).

- **Cost-Aware Routing**
  - Classify intents as **trivial vs. complex** using a lightweight classifier:
    - Trivial → cheap models / local models where possible.
    - Complex / high-risk → frontier models for generation, local models for judging.
  - Track:
    - Per-node token usage.
    - Model selection decisions.
  - Incorporate budget limits and per-project policies.

- **Global Repository Rules & Policy Engine**
  - Central config capturing:
    - Security rules (e.g. no direct DB access from UI).
    - Performance constraints.
    - Coding standards and architectural guidelines.
  - Enforce through:
    - Intent Engine (task specifications).
    - Judge prompts (must enforce rules).
    - DAG-level guardrails (e.g. static analysis blockers).

- **Advanced Control Plane Features (Optional Layer 3 Enhancements)**
  - **Memory Summarizer Node**:
    - Periodically compress execution logs into semantic summaries to avoid context bloat.
  - **“Supreme Court” Frontier Model Router**:
    - Resolve repeated disagreements between coder and specialized reviewers.
  - **Hybrid Escalation Workflows**:
    - Hard-coded tripwires (loop limits, budget caps).
    - Semantic “raise hand” tool for agents to request human help.

**Testing**

- Run a DAG where:
  - Code must compile and tests must pass **inside** the ephemeral container.
  - Introduce failures (e.g. failing tests, security rule violations).
- Verify:
  - Sandboxes isolate all effects; main repo and environments stay clean.
  - Judge decisions incorporate test logs and policy violations.
  - Cost metrics are logged and routing decisions are visible.
  - Violations trigger guardrails and escalation rather than silent failure.

---

## 7. Deliverables Checklist by Phase

- **Phase 1**
  - `repo_map` generator.
  - AST parsers and RAG retrieval.
  - Single-file diff agent + basic syntax validation.

- **Phase 2**
  - Checkpointing + rollback.
  - Local quantized Judge service and strict prompt.
  - Verification loop with retries and halt conditions.
  - LangGraph-based implementation in `phase2/verification_loop.py` with a demo CLI in `phase2/cli.py`.

- **Phase 3**
  - Intent Engine → 3–5 node DAG compiler.
  - DAG executor wired to the Phase 2 loop.
  - State persistence and event emission.

- **Phase 4**
  - Kanban / DAG visualization.
  - Context Injector modal.
  - Tri-pane review cockpit wired to semantic tracing.

- **Phase 5**
  - Ephemeral Docker sandbox integration.
  - Cost-aware routing and model selection.
  - Global policy engine (security/perf/coding rules) feeding into the Judge and DAG.

