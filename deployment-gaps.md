# Agenti-Helix: Deployment Gaps vs. Architecture Blueprint

**Evaluation Date:** April 6, 2026  
**Last Updated:** April 6, 2026 — full Layer 1–4 + cross-cutting implementation session  
**Blueprint:** *Agenti-Helix Architecture Blueprint.md*  
**Codebase branch state:** post-`single_agent` deletion; Layers 1–4 and cross-cutting gaps fully implemented

---

## Assessment Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Implemented and functional |
| ⚠️ | Partially implemented — functional but incomplete or demo-grade |
| ❌ | Missing — not implemented; blocks stated blueprint goals |
| 🔒 | Security risk — must resolve before any non-local deployment |

---

## Quick-Reference Gap Summary

> **Session status:** All P0/P1/P2 gaps and cross-cutting concerns implemented.  
> Remaining open items are §4.2 (ephemeral sandbox) which requires Docker infrastructure.

| Layer / Section | Status | Severity |
|-----------------|--------|----------|
| L1 — Requirements Ingestion | ✅ | — |
| L1 — DAG Generation (LLM path) | ✅ | — |
| L1 — DAG Generation (deterministic path) | ✅ | — |
| L1 — AST-Aware Repository Map | ✅ | — |
| L1 — AST Chunking (full class/function) | ✅ | — |
| L1 — Dependency Graphing (call-graph RAG) | ✅ | — |
| L2 — Pluggable Agent Provisioning | ✅ | — |
| L2 — Execution Checkpointing | ✅ | — |
| L2 — Long-Term / Episodic Memory | ✅ | — |
| L3 — Semantic Tracing | ✅ | — |
| L3 — Autonomous Verification Loop | ✅ | — |
| L3 — Static Checks in Verification | ✅ | — |
| L3 — Auto-Correction Routing | ✅ | — |
| L4 — Trust Dashboard (DAG visualization) | ✅ | — |
| L4 — Diff & Intent Split-Screen | ✅ | — |
| §4.1 — Cost-Aware / Multi-Backend Routing | ✅ | — |
| §4.2 — Ephemeral Sandboxing | ❌ | Critical |
| §4.3 — Context Pruning / Memory Summarization | ✅ | — |
| §4.4 — Supreme Court Consensus Router | ✅ | — |
| §4.5 — Hybrid Escalation (local gen uncapped) | ✅ | — |
| §4.5 — Hybrid Escalation (bandit security scan) | ✅ | — |
| §4.5 — Hybrid Escalation (semantic "Raise Hand") | ✅ | — |
| §4.6 — Semantic Git Blame / Traceability | ✅ | — |
| D1 — Authentication & Authorization | ✅ | — |
| D2 — Judge Service Isolation | ✅ | — |
| D3 — Environment / Config Documentation | ✅ | — |
| D4 — Repo Scanner Ignore Rules | ✅ | — |
| D5 — Polling Performance (TTL cache) | ✅ | — |
| D6 — Topbar Search Wired | ✅ | — |
| D7 — Stale Package Documentation | ✅ | — |

---

## Layer 1 — Intent & Context Engine

### 1.1 Requirements Ingestion  
**Status:** ✅ **Implemented**

`intent_compiler.py` accepts a macro intent string and compiles it into a DAG via an LLM-based chain with retry logic (up to 3 attempts) and Pydantic validation, plus a deterministic fallback. Structured error injection between retries enables self-correction.

---

### 1.2 DAG Generation (LLM Path)  
**Status:** ✅ **Implemented**

`compile_macro_intent_with_llm()` in `intent_compiler.py`:
- `_run_intent_chain` helper passes `feedback` string between retries
- Pydantic `_IntentCompilerOutputModel` validates every LLM response
- Raises `ValueError` with full error trail after `_MAX_COMPILE_RETRIES` failures
- Silent demo-DAG fallback removed — errors surface as HTTP 422

---

### 1.3 Dependency Graphing  
**Status:** ✅ **Implemented**

`core/repo_map.py`:
- `_resolve_import_to_path` handles Python-style relative imports (`.module` → `./module`, `..module` → `../module`)
- `build_dependency_graph()` builds a file→deps dict from import declarations
- `get_focused_files(repo_map, target_files, depth=N)` returns target + N-hop dependencies
- `tool_get_focused_context` exposed in `TOOL_REGISTRY`; replaces full-map context in `default_coder_chain`

---

## Layer 2 — Agent Orchestration Hub

### 2.1 Pluggable Agent Provisioning  
**Status:** ✅ **Implemented**

- `AgentSpec.render()` generalized to dispatch all roster agents via `render_prompt(template, raw_input)`
- `backend_type` field on `AgentSpec` can route per-agent to `mlx_local` or `openai` (default: all agents use `mlx_local` + shared `QWEN_MODEL_PATH`)
- `OpenAIChatBackend` implemented in `inference_backends.py`
- `memory_summarizer_v1`, `supreme_court_v1` registered; `judge_v1` pinned to `mlx_local`

---

### 2.2 Execution Checkpointing  
**Status:** ✅ **Implemented**

`checkpointing.py` `rollback_to_checkpoint()` now:
- Restores file content from `pre_state_ref`
- Resets `checkpoint.status = RUNNING`
- Clears `post_state_ref` and `diff`
- Persists updated checkpoint to disk via `save_checkpoint()`

---

### 2.3 Episodic Memory  
**Status:** ✅ **Implemented**

`memory/` package:
- `MemoryStore`: JSONL-backed persistence, token-based Jaccard similarity search
- `index_resolved_episode()` / `index_from_verification_state()` index resolved episodes
- `tool_query_memory` exposed in `TOOL_REGISTRY`
- `GET /api/memory` queries the live store

---

## Layer 3 — Observability & Verification Fabric

### 3.1 Semantic Tracing  
**Status:** ✅ **Implemented**

- `log_event` accepts `trace_id` and `dag_id`; emits both as top-level JSON fields
- `execute_dag` generates a unique `trace_id` per execution; propagates through all `log_event` calls
- `GET /api/events` accepts `?traceId=` and `?dagId=` filter params

---

### 3.2 Autonomous Verification Loop  
**Status:** ✅ **Functional** (LangGraph-based)

Pre-checkpoint → Coder → Static Checks → Judge → Verdict → Retry/Supreme Court/BLOCKED.  
Cooperative cancellation via `TaskCancelledError` wired through `chain_runtime` and `agent_runtime`.

---

### 3.3 Static Checks  
**Status:** ✅ **Implemented**

`_run_static_checks()` in `verification_loop.py`:
- Python: `py_compile` (syntax) + `ruff check --select E,F` (lint) + `bandit` (security, §4.5)
- JS/TS: `node --check` (syntax)
- Returns `{passed, errors, checks_run, security_blocked}`; pipes into LangGraph routing
- Security findings with `security_blocked=True` bypass the retry loop → immediate BLOCKED

---

### 3.4 Auto-Correction Routing  
**Status:** ✅ **Implemented**

- `TaskCancelledError` raised correctly in `chain_runtime` and `agent_runtime`
- `cancel_token` propagated from `run_chain` → `run_agent`
- Token budget exceeded also triggers human escalation short-circuit

---

## Layer 4 — Human Control Interface

### §4.1 — Cost-Aware Routing & Local Models as Judges  
**Status:** ✅ **Implemented**

- `OpenAIChatBackend` in `inference_backends.py` with `httpx`; respects `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`
- `AgentSpec.backend_type` field; `judge_v1` and `supreme_court_v1` → `mlx_local` (same shared model); `openai` remains optional via env
- `get_default_inference_backend(backend_type=...)` dispatches accordingly

---

### §4.2 — Ephemeral Sandboxing  
**Status:** ❌ **Not implemented** (P0 — requires Docker)

**Remaining gap:** Agents still write directly to the working tree. The rollback mechanism in `checkpointing.py` provides a safety net, but true isolation requires a Docker sandbox.

**Recommended approach:**
1. Add `docker` Python SDK to `requirements.txt`
2. Create `backend/agenti_helix/sandbox/` with `SandboxContainer` and `SandboxManager`
3. Replace direct filesystem writes with sandbox-scoped writes; flush to working tree only on `passed=True`
4. Gate with `AGENTI_HELIX_SANDBOX_ENABLED=true`

---

### §4.3 — Context Pruning / Memory Summarization  
**Status:** ✅ **Implemented**

- `VerificationState` gains `error_history: List[str]`, `compressed_context: Optional[str]`
- `node_summarize_context` LangGraph node calls `memory_summarizer_v1` when `retry_count >= 1`
- Compressed context threaded into coder intent via `_build_coder_intent()`
- Raw `error_history` hard-capped at 3× `max_error_history_chars` (keeps last 5 entries)
- `VerificationConfig.max_error_history_chars = 4_000`

---

### §4.4 — Supreme Court Consensus Router  
**Status:** ✅ **Implemented**

- `SupremeCourtOutput` Pydantic model with `resolved`, `reasoning`, patch fields
- `supreme_court_v1` agent registered with `backend_type="mlx_local"` and `agents/prompts/supreme_court.md` prompt (same local model as coder/judge)
- `node_supreme_court` LangGraph node: invoked when `retry_count >= max_retries` and SC not yet tried
- On `resolved=True`: applies patch via `tool_apply_line_patch_and_validate`, routes to `run_static_checks`
- On `resolved=False`: records BLOCKED with SC error in `tool_logs`
- `state.supreme_court_invoked` prevents re-entry; `VerificationConfig.supreme_court_enabled = True` gates feature
- LangGraph routing: `handle_verdict → supreme_court` (SC enabled) or `END` (SC disabled / SC tried)

---

### §4.5 — Hybrid Escalation Workflows  
**Status:** ✅ **Implemented**

**Local generation (no app-level token cap):**
- Chains and `run_agent` omit `max_tokens` by default; MLX uses a large generation ceiling (`AGENTI_HELIX_MLX_MAX_TOKENS`, default 262144) so output is effectively limited only by model context and EOS.
- The previous verification-loop character budget guardrail was removed so local runs are not cut off mid-task.

**Security guardrail:**
- `_check_bandit_security()` runs bandit for Python files; critical/high findings set `security_blocked=True`
- Static check routing short-circuits to BLOCKED immediately on `security_blocked` (bypasses judge and SC)

**"Raise Hand" tool:**
- `tool_escalate_to_human(reason, blocker_summary)` added to `TOOL_REGISTRY`
- `CoderPatchOutput` gains optional `escalate_to_human: bool` and `escalation_reason: str` fields
- `coder_patch.md` updated with escalation protocol JSON format
- `_coder_ok` routing checks `state.human_escalation_requested` and short-circuits to BLOCKED

---

### §4.6 — Semantic Git Blame / Traceability  
**Status:** ✅ **Implemented**

- `backend/agenti_helix/api/git_ops.py`: `real_git_commit()` and `git_blame_line()`
- Merge endpoint now calls `real_git_commit()` with `Trace-Id:`, `Dag-Id:`, `Intent:` git trailers
- Merge record JSON stores `commit_sha` and `git_simulated` flag
- `AGENTI_HELIX_GIT_COMMIT_ENABLED=true` activates real commits (simulated by default in dev)
- `GET /api/blame?file=<path>&line=<n>` endpoint: uses `git_blame_line` + falls back to merge record lookup
- `gitpython>=3.1.0` added to `requirements.txt`

---

## Cross-Cutting Deployment Concerns

### D1 — Authentication & Authorization  
**Status:** ✅ **Implemented**

`backend/agenti_helix/api/auth.py`:
- `require_auth` FastAPI dependency validates `Authorization: Bearer <token>` against `AGENTI_HELIX_API_KEY`
- `require_editor` dependency adds role gating for mutation endpoints
- Two roles: `editor` (full access) and `viewer` (read-only via `AGENTI_HELIX_VIEWER_API_KEY`)
- Auth bypassed in dev mode when `AGENTI_HELIX_API_KEY` is unset; enforced with `AGENTI_HELIX_AUTH_ENABLED=true`
- `PUT /api/agents/{agent_id}/prompt`, `POST /api/dags/run`, `POST /api/tasks/merge` require `editor` role
- CORS `allow_headers` updated to include `Authorization`
- Frontend `api.ts`: `_authHeaders()` injects `Bearer ${VITE_API_KEY}` centrally; 401/403 surfaces clear error message

---

### D2 — Judge Service Isolation  
**Status:** ✅ **Implemented**

`judge_server.py`:
- `CORSMiddleware` restricted to `["http://127.0.0.1:8001", "http://localhost:8001"]`; wildcard `"*"` removed
- Raw `print()` debug statements replaced with structured `log_event()` calls
- Deployment note: bind to `127.0.0.1` via `--host 127.0.0.1` uvicorn flag (enforced in `scripts/start-dev.sh`)

---

### D3 — Environment Variables & Configuration  
**Status:** ✅ **Implemented**

- `backend/.env.example`: all backend vars with inline comments (repo root, inference, auth, observability, git)
- `frontend/.env.example`: `VITE_API_BASE_URL` and `VITE_API_KEY`
- `scripts/start-dev.sh`: starts judge (port 8000), control-plane (port 8001), and frontend (port 5173); accepts `--repo` flag; loads `.env` automatically
- `README.md` "Quick Start" section added with prerequisites, install, configure, and launch steps

---

### D4 — Repo Scanner: Missing Ignore Rules  
**Status:** ✅ **Implemented**

`core/repo_scanner.py`:
- `IGNORE_DIRS: FrozenSet[str]` constant exported; includes `node_modules`, `.git`, `__pycache__`, `.venv`, `dist`, `build`, `.agenti_helix`, `.next`, and others
- `_is_ignored()` checks every path component against the combined ignore set
- `scan_repository()` gains `exclude_patterns: Optional[List[str]]` for per-project overrides
- Prevents indexing tens-of-thousands of files from `node_modules`

---

### D5 — Polling Performance  
**Status:** ✅ **Implemented**

`api/main.py`:
- `GET /api/features` and `GET /api/triage` wrapped with 5-second `TTLCache` (from `cachetools`)
- Graceful fallback: imports guarded by `try/except ImportError`; `_CACHE_AVAILABLE` flag controls usage
- `cachetools>=5.3` added to `requirements.txt`
- `GET /api/events` already supports `?sinceTs=` cursor-based filtering

---

### D6 — Topbar Search  
**Status:** ✅ **Implemented**

`frontend/src/App.tsx` — `FeaturesKanbanPage`:
- `useSearchParams` imported and wired; reads `?q=` from URL
- `filteredFeatures` memoized: filters by `f.title` or `f.dag_id` containing the search query (case-insensitive)
- Kanban board renders `filteredFeatures` instead of raw `features`

---

### D7 — Stale Package Documentation  
**Status:** ✅ **Implemented**

- `backend/agenti_helix/__init__.py` docstring updated: removed `single_agent` reference; reflects current `runtime/`, `memory/`, `agents/`, `api/`, `core/`, `verification/`, `orchestration/`, `observability/` layout
- `requirements.txt` now explicitly lists `pydantic>=2.0`, `httpx>=0.27`, `cachetools>=5.3`, `gitpython>=3.1.0`, `bandit>=1.7.0`

---

## Prioritized Deployment Roadmap

### P0 — Blockers (must resolve before any non-local use)

| ID | Action | File(s) | Status |
|----|--------|---------|--------|
| D1 | Authentication on all API endpoints | `api/main.py`, `api/auth.py`, `frontend/src/lib/api.ts` | ✅ Done |
| D2 | Judge service CORS isolation; remove debug prints | `verification/judge_server.py` | ✅ Done |
| §4.2 | Ephemeral sandbox (Docker) | `runtime/tools.py`, new `sandbox/` package | ❌ Remaining |

### P1 — High-Impact Gaps (limits blueprint-stated value propositions)

| ID | Action | File(s) | Status |
|----|--------|---------|--------|
| §4.6 | Real `git commit` + trace metadata | `api/task_commands_routes.py`, `api/git_ops.py` | ✅ Done |
| §4.1 | Cloud inference backend for coder; local for judge | `runtime/inference_backends.py`, `agents/registry.py` | ✅ Done |
| 3.3 | Static checks (syntax + linter + security) | `verification/verification_loop.py` | ✅ Done |
| 2.1 | Generalize agent render; enable all roster agents | `agents/registry.py` | ✅ Done |
| 3.1 | Thread `trace_id`; add `GET /api/events` filters | `verification/verification_loop.py`, `api/main.py` | ✅ Done |
| D3 | `.env.example` files and `scripts/start-dev.sh` | repo root | ✅ Done |
| 1.4 | Dependency graph; focused context tool | `core/repo_map.py`, `runtime/tools.py` | ✅ Done |

### P2 — Medium-Impact (architectural completeness)

| ID | Action | File(s) | Status |
|----|--------|---------|--------|
| §4.5 | Bandit security scan; `tool_escalate_to_human`; uncapped local MLX generation | `runtime/tools.py`, `verification/verification_loop.py`, `runtime/inference_backends.py` | ✅ Done |
| §4.3 | Memory summarizer node in LangGraph (retry ≥ 1) | `verification/verification_loop.py` | ✅ Done |
| §4.4 | Supreme Court node (frontier arbitration before ESCALATE) | `verification/verification_loop.py`, `agents/registry.py` | ✅ Done |
| 2.3 | Episodic memory store (JSONL + Jaccard similarity) | `memory/` package | ✅ Done |
| 2.2 | Rollback resets checkpoint status and clears post-state | `verification/checkpointing.py` | ✅ Done |
| 3.4 | `TaskCancelledError` wired through chain and agent runtime | `runtime/chain_runtime.py`, `runtime/agent_runtime.py` | ✅ Done |
| D4 | Ignore dirs in repo scanner | `core/repo_scanner.py` | ✅ Done |
| D5 | TTL caching for derived views | `api/main.py` | ✅ Done |

### P3 — Polish & Completeness

| ID | Action | File(s) | Status |
|----|--------|---------|--------|
| 1.3 | Remove silent demo DAG fallback | `orchestration/intent_compiler.py` | ✅ Done |
| 1.1 | `acceptance_criteria` in DagNode, threaded to judge | `orchestration/orchestrator.py` | ✅ Done |
| §4.6 | `GET /api/blame`; merge record fallback | `api/main.py`, `api/git_ops.py` | ✅ Done |
| D6 | Wire topbar search `q` param in `FeaturesKanbanPage` | `frontend/src/App.tsx` | ✅ Done |
| D7 | Update stale `__init__.py`; pin `requirements.txt` | `backend/agenti_helix/__init__.py`, `requirements.txt` | ✅ Done |
| §4.2 | Ephemeral Docker sandbox | new `sandbox/` package | ❌ Remaining |

---

## Remaining Open Item

### §4.2 — Ephemeral Sandboxing (Docker)

This is the sole remaining P0 item. All other gaps across Layers 1–4 and cross-cutting concerns have been closed.

**What remains:**
1. Add `docker` Python SDK to `requirements.txt`
2. Create `backend/agenti_helix/sandbox/container.py` — `SandboxContainer` with `apply_patch()`, `run_checks()`, `destroy()`
3. Create `backend/agenti_helix/sandbox/manager.py` — context manager that creates/destroys containers around the coder chain
4. Replace direct filesystem write in `tool_apply_line_patch_and_validate` with sandbox-scoped write
5. Gate with `AGENTI_HELIX_SANDBOX_ENABLED=true` env var

The existing `checkpointing.py` rollback mechanism provides an interim safety net.
