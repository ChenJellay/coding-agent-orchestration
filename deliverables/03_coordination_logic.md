# Coordination Logic

Who starts the process, how handoffs happen between components, where a human may intervene, and how the system decides to stop.

---

## 1. How It Starts

A task begins in one of two ways:

**Via the UI (Dashboard):** The user types a `macro_intent` (e.g. "Refactor the header component to use CSS variables"), selects a pipeline mode (Patch / Build / Orchestrator), and clicks "Submit command." The frontend calls `POST /api/dags/run`.

**Via the CLI:** `python -m agenti_helix.orchestration.cli run --intent "..." --repo-path /path/to/repo` calls the same compilation and execution logic directly.

In both cases, the API handler returns immediately with `{"ok": true, "dag_id": "dag-ui-run-..."}`. The user sees the `dag_id` in the UI. All actual work happens in a background daemon thread.

---

## 2. Compilation Handoff

The background thread runs `_compile_and_execute(cancel_token)`:

1. Calls `compile_macro_intent_to_dag(macro_intent, repo_path, use_llm=...)`.
2. If `use_llm=True`: runs the intent compiler chain (`build_repo_map_context` → `intent_compiler_v1`). Up to 2 retries on Pydantic validation failure; each retry includes error feedback in the prompt.
3. If `use_llm=False`: uses the deterministic demo compiler (hardcoded 3-node DAG for the demo repo).
4. **On failure:** logs the error to `events.jsonl` and exits the background thread. The `dag_id` will not appear in the features board.
5. **On success:** applies any `pipeline_mode` override from the request body, persists `DagSpec` to disk, invalidates the UI cache, then calls `execute_dag(spec)`.

The compilation result is a `DagSpec` — a validated DAG where every node has a resolved target file, intent, and acceptance criteria.

---

## 3. DAG Execution Handoffs

`execute_dag(spec)` in `orchestrator.py`:

1. Calls `_topological_order(spec.nodes, spec.edges)` — Kahn's algorithm, raises on cycles.
2. Iterates nodes in topological order. For each node:
   - Sets node state to `RUNNING`, persists state file.
   - Checks whether all predecessor nodes have status `PASSED_VERIFICATION`. If any predecessor failed, marks this node `FAILED` (cascade) and skips execution.
   - Calls `run_verification_loop(node.task, config, cancel_token, trace_id, dag_id)`.
   - On return: reads the final `VerificationState` and maps it to a `DagNodeStatus`.
3. After all nodes complete, persists the final `DagExecutionResult`.

**Dependency enforcement:** A node only runs when all edges pointing to it have resolved to PASSED_VERIFICATION. If any predecessor is FAILED or BLOCKED, the dependent node is cascade-failed without invoking the verification loop.

---

## 4. Verification Loop — Step-by-Step Handoffs

Each node runs the LangGraph state machine in `verification_loop.py`. The graph has 7 nodes with conditional routing:

### Step 1: `take_pre_checkpoint`
Snapshots the target file content (`snapshot_target_file`). Creates a `Checkpoint` with `status=PENDING` and persists it. Sets `state.original_content`. Always transitions to `run_coder`.

### Step 2: `run_coder`
Resolves the coder chain via `resolve_coder_chain(task)`:
- If `task.coder_chain` is set (explicit override): use it.
- Else if `pipeline_mode == "build"`: use `default_full_pipeline_coder_chain()`.
- Else: use `default_coder_chain()` (fast patch).

Executes the chain. Injects `compressed_context` into the intent if this is a retry (retry ≥ 1).

**Escalation check:** If the coder output contains `escalate_to_human=True`, sets `state.human_escalation_requested=True`. The `run_static_checks` node will detect this and route to END (BLOCKED).

**On chain error:** Sets `state.coder_error`, records error in `state.error_history`. Routes to `handle_verdict` as a FAIL.

### Step 3: `run_static_checks`
Runs language-specific checks on the target file:
- **Python:** `py_compile` (syntax), `ruff` (E, F rules), `bandit` (security, high severity only).
- **JS/TS:** `node --check` (syntax).

**Security escalation (§4.5):** If bandit reports a critical finding, sets `security_blocked=True` in static_check_logs. The routing function detects this and transitions directly to END (BLOCKED) — no judge is called, no retry is offered.

**Human escalation exit:** If `state.human_escalation_requested` is True, transitions to END (BLOCKED).

Otherwise: transitions to `call_judge`.

### Step 4: `call_judge`
Resolves the judge chain via `resolve_judge_chain(task)` using the same mode logic as the coder. Executes the chain, passing intent, diff, acceptance criteria, and static check logs.

**On chain error:** Treats as FAIL verdict.

Transitions to `handle_verdict`.

### Step 5: `handle_verdict`
The routing hub. Reads `state.judge_response.verdict`:

**If PASS:**
- Updates checkpoint with `status=PASSED`, `post_state_ref` (current file content), `diff`, and `tool_logs`.
- Indexes a memory episode if `retry_count > 0` (a learned resolution).
- Transitions to END. Node exits with `PASSED_VERIFICATION`.

**If FAIL and `retry_count < max_retries` (default: 2):**
- Appends the judge's justification to `state.error_history` (capped to 3 entries).
- Rolls back the target file to `original_content`.
- Increments `retry_count`.
- If `retry_count >= 2`: transitions to `summarize_context`.
- Else: transitions directly back to `run_coder`.

**If FAIL and retries exhausted:**
- If `supreme_court_enabled=True` and SC not yet invoked: transitions to `node_supreme_court`.
- Else: records checkpoint with `status=BLOCKED`, transitions to END.

### Step 6: `summarize_context` (invoked on retry ≥ 2)
Calls `memory_summarizer_v1` with the raw error history. On success: stores the `compressed_summary` + `key_constraints` as `state.compressed_context`. On failure: falls back to the raw feedback string. Transitions to `run_coder`.

### Step 7: `supreme_court` (invoked on exhausted retries)
Calls `supreme_court_v1` with the full failure context. Sets `state.supreme_court_invoked=True`.

- **If `resolved=True`:** Applies the SC's patch to the target file. Transitions to `run_static_checks` (re-validates from scratch).
- **If `resolved=False`:** Records checkpoint with `status=BLOCKED`. Transitions to END.

---

## 5. Human Intervention Points

The system surfaces multiple explicit points where a human can intervene:

| Trigger | UI Action | API Call | Effect |
|---------|-----------|----------|--------|
| Node is BLOCKED | Triage Inbox shows it | — | Human identifies the issue |
| Want to retry with guidance | Task Intervention → "Apply + re-run" | `POST /api/tasks/apply-and-rerun` | Guidance injected as compressed_context; retries reset |
| Want to retry without guidance | Task Intervention → "Re-run from checkpoint" | `POST /api/tasks/rerun` | Resets retry count, re-enters verification loop |
| Attach reference document | Task Intervention → doc URL field | `POST /api/tasks/context` | doc_url saved; doc_fetcher_v1 can consume it on next run |
| Cancel a running task | Task Intervention → "Abort" | `POST /api/tasks/abort` | Sets cancel token; loop exits gracefully at next check |
| Edit the macro intent | Sign-Off → "Edit intent" | `PUT /api/dags/{id}/intent` | Updates DagSpec (does not automatically rerun) |
| Accept and merge | Sign-Off → "Merge to main" | `POST /api/tasks/merge` | Commits the post-state checkpoint to the target branch |

**Coder-initiated escalation (§4.5):** The coder agent itself can raise a hand by including `escalate_to_human: true` in its output. This signals ambiguous requirements or a conflict the coder cannot resolve without clarification. The node goes BLOCKED immediately — no retries, no Supreme Court. It appears in the Triage Inbox for human review.

---

## 6. How the System Decides What to Do Next

Every routing decision in the verification loop is a pure function of the current `VerificationState`. The logic is:

```
After coder:
  escalate_to_human=True  →  BLOCKED (skip checks, skip judge)
  coder_error             →  FAIL verdict (go to handle_verdict)
  else                    →  static checks

After static checks:
  security_blocked=True   →  BLOCKED (skip judge, skip retry)
  else                    →  judge

After judge:
  PASS                    →  record PASSED, index memory
  FAIL, retries left      →  rollback, increment count, (compress if ≥2), retry coder
  FAIL, retries done, SC  →  invoke supreme_court
  FAIL, retries done, !SC →  record BLOCKED

After supreme court:
  resolved=True           →  re-enter static checks
  resolved=False          →  record BLOCKED
```

The DAG orchestrator's decision is simpler:
- All predecessors PASSED → run this node.
- Any predecessor FAILED or BLOCKED → cascade-fail this node.
- All nodes terminal → DAG is done.

---

## 7. How the Process Stops

The system reaches a terminal state through one of four paths:

**Normal completion:** All nodes in the DAG reach `PASSED_VERIFICATION`. The feature column transitions to `READY_FOR_REVIEW`. The user reviews the diff in the Sign-Off view and optionally merges to main.

**Partial failure:** One or more nodes reach `BLOCKED` (exhausted retries or escalation). Dependent downstream nodes are cascade-failed. The DAG halts for those nodes. Other branches that don't depend on the failed node continue. The Triage Inbox surfaces blocked items. A human can re-run with guidance.

**Cancellation:** The user calls `POST /api/tasks/abort` or sets the cancel token externally. The active chain checks `cancel_token.is_set()` at the next step boundary and exits. The checkpoint is recorded as BLOCKED. The background thread exits cleanly.

**Background thread crash:** Unhandled exceptions in the background thread are caught at the top-level executor wrapper, which logs the error and allows the thread to exit. The DAG state file may be incomplete, and affected nodes will stay RUNNING until the user re-triggers or the server restarts.

---

## 8. Observability During Execution

The system does not push real-time events to the UI. The UI polls:
- `GET /api/features` — every 5 seconds (Kanban column + confidence + ETA)
- `GET /api/features/{id}` — every 2.5 seconds when viewing a DAG (node statuses)
- `GET /api/events?runId=...` — per-task execution logs
- `GET /api/events/stream` — SSE stream (15-second heartbeat, polling-based under the hood)
- `GET /api/checkpoints?task_id=...` — pre/post state snapshots

Every significant step in the verification loop emits a `log_event()` call with `run_id`, `hypothesis_id`, `trace_id`, `dag_id`, `location`, and structured `data`. These land in `events.jsonl` and are queryable immediately via the API.
