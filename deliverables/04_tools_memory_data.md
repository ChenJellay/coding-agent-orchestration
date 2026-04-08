# Tools, Memory, and Data Design

---

## 1. Tools

Tools are Python functions registered in `TOOL_REGISTRY` (`backend/agenti_helix/runtime/tools.py`). They are invoked by the chain runtime when a chain step has `"type": "tool"`. Each tool returns a `Dict[str, Any]` that is merged into the running chain context under the step's `output_key`.

### Full Tool Registry (14 tools)

| Tool Name | Parameters | Returns | Used In | Called By |
|-----------|-----------|---------|---------|-----------|
| `build_repo_map_context` | `repo_root` | `repo_files[]`, `repo_map_json`, `allowed_paths` | Patch + Build | intent_compiler chain |
| `get_focused_context` | `repo_root`, `target_files[]`, `depth=1` | Same as above (filtered) | Patch | coder_patch chain |
| `build_ast_context` | `repo_root`, `target_files[]` | `ast_repo_map_json` (depth=2 focused) | Build | librarian chain |
| `load_file_contents` | `repo_root`, `required_files[]` | `file_contexts_json` (JSON string) | Build | librarian → coder chain |
| `snapshot_target_file` | `repo_root`, `target_file` | `str` (raw file content) | Patch | coder + judge chains |
| `infer_language_from_target_file` | `target_file` | `str` (language identifier) | Patch | judge chain |
| `apply_line_patch_and_validate` | `repo_root`, `patch: Dict`, `allowed_paths[]` | Validated patch dict or `{escalated: True}` | Patch | coder chain |
| `write_all_files` | `repo_root`, `modified_files[]`, `test_files[]` | `files_written[]`, `test_file_paths[]`, `diff_json_str` | Build | coder_builder chain |
| `run_tests` | `repo_root`, `test_file_paths[]` | `passed: bool`, `terminal_logs`, `test_count` | Build | judge chain |
| `load_rules` | `repo_root` | `repo_rules_text` (JSON string) | Build | judge chain |
| `build_tool_logs_json` | `static_check_logs: Dict` | `str` (JSON-serialized logs) | Patch | judge chain |
| `map_evaluator_verdict` | `pass_tests`, `evaluation_reasoning`, `feedback_for_coder`, `is_safe`, `violations[]` | `verdict`, `justification`, `problematic_lines[]` | Build | judge chain (final step) |
| `query_memory` | `error_description`, `top_k=3` | `episodes[]` (episode_id, error_text, resolution, task_id) | Both | Optional coder context |
| `escalate_to_human` | `reason`, `blocker_summary` | `escalation_requested: True`, `reason`, `blocker_summary` | Both | Coder agent output |

### Tool Design Principles

- **No side effects beyond writing to `repo_root`** — tools are scoped to the target repository and `.agenti_helix/` metadata directories.
- **Graceful failure** — tools return structured error info rather than raising exceptions where possible (e.g. `tool_load_file_contents` returns `"# File not found: {path}"` on missing files).
- **Context passthrough** — every tool result is merged into the chain context dict so downstream steps can reference it via `{"$ref": "output_key.field"}`.
- **Idempotent where possible** — `write_all_files` creates parent directories and overwrites; safe to call again after a rollback.

### Chain DSL — How Tools Are Invoked

```json
{
  "steps": [
    {
      "type": "tool",
      "id": "ctx_step",
      "output_key": "focused_context",
      "tool_name": "get_focused_context",
      "input_bindings": {
        "repo_root": {"$ref": "repo_root"},
        "target_files": [{"$ref": "target_file"}]
      }
    },
    {
      "type": "agent",
      "id": "coder_step",
      "output_key": "coder_patch",
      "agent_id": "coder_patch_v1",
      "input_bindings": {
        "repo_map_json": {"$ref": "focused_context.repo_map_json"},
        "intent": {"$ref": "intent"}
      }
    }
  ]
}
```

The `chain_runtime` resolves `$ref` paths from the current context, calls the tool or agent, and stores the result under `output_key` for the next step.

---

## 2. Memory Design

### Purpose
Episodic memory allows the system to learn from past task resolutions. When a coder fails on the first attempt but succeeds after retry, the error and resolution are stored. Future tasks facing similar errors can query this store to pre-load relevant strategies into their context.

### Episode Data Model

Defined in `backend/agenti_helix/memory/store.py`:

```python
@dataclass
class Episode:
    episode_id: str        # UUID — unique identifier
    task_id: str           # Task that resolved this error
    dag_id: str            # Parent DAG for traceability
    error_text: str        # Judge justification or error message from the failed attempt
    resolution: str        # Description of the patch or fix that succeeded
    target_file: str       # File that was edited (for retrieval filtering)
    created_at: float      # Unix timestamp
    metadata: Dict         # Extensible: retry_count, agent IDs, etc.
```

### Storage
- **File:** `.agenti_helix/memory/episodes.jsonl`
- **Format:** Append-only JSONL (one JSON object per line)
- **Writes:** Atomic append — no in-place modification

### Query Mechanism
Jaccard similarity over word tokens:
1. Tokenise the query string and each episode's `error_text` (lowercase, punctuation stripped).
2. Compute intersection / union of token sets.
3. Return top-K episodes ranked by score.

**Current limitations:**
- Token overlap is approximate — misses semantic similarity (e.g. "TypeError" vs "type mismatch")
- No TTL or quality gate — stale or incorrect episodes accumulate over time
- No per-file filtering in the similarity score

**Upgrade path:** Replace `store.py` with a vector DB backend (Chroma, Qdrant) using embedding similarity. The `query_memory` tool API is unchanged; only the store implementation changes.

### When Episodes Are Written
`index_from_verification_state(state)` is called at the end of each verification loop run:
- **Written** only if `state.retry_count > 0` AND final verdict is `PASSED`.
- **Not written** on first-attempt PASS (trivial success adds noise).
- **Not written** on BLOCKED (no successful resolution to learn from).
- The episode's `error_text` is the judge's final rejection justification before the resolution; `resolution` is a summary of the successful patch.

---

## 3. Data Design

### Directory Structure

All persistent state lives under a single directory rooted at `AGENTI_HELIX_REPO_ROOT`:

```
.agenti_helix/
├── dags/
│   ├── {dag_id}.json              ← DagSpec: macro_intent, nodes, edges
│   └── {dag_id}_state.json        ← DagNodeExecutionState: node statuses, attempt counts
├── checkpoints/
│   └── {checkpoint_id}.json       ← Checkpoint: pre/post snapshots, diff, tool_logs
├── memory/
│   └── episodes.jsonl             ← Episodic memory store (append-only)
├── logs/
│   └── events.jsonl               ← Structured event log (append-only)
├── task_context/
│   └── {task_id}.json             ← User-attached doc URLs and notes
└── rules.json                     ← Repo compliance rules (optional)
```

### Key Data Model Schemas

**`DagSpec`** (persisted as `dags/{dag_id}.json`):
```
dag_id: str
macro_intent: str
nodes: {
  node_id: {
    node_id: str
    description: str
    task: EditTaskSpec
  }
}
edges: [[src_node_id, dst_node_id], ...]
```

**`EditTaskSpec`** (embedded in DagSpec nodes):
```
task_id: str              — "{dag_id}:{node_id}"
intent: str               — refined intent for the coder (macro + subtask)
target_file: str          — relative path within repo
acceptance_criteria: str  — testable success condition
repo_path: str            — absolute path to repo root
pipeline_mode: str        — "patch" | "build"
coder_chain: Dict | null  — explicit chain override (optional)
judge_chain: Dict | null  — explicit chain override (optional)
```

**`Checkpoint`** (persisted as `checkpoints/{checkpoint_id}.json`):
```
checkpoint_id: str        — UUID
task_id: str
status: str               — PENDING | RUNNING | PASSED | BLOCKED
pre_state_ref: str        — full file content before any edits
post_state_ref: str|null  — full file content after edits (set on verdict)
diff: str|null            — JSON-serialized diff (the patch applied)
tool_logs: {
  judge: {verdict, justification, problematic_lines}
  static_checks: {passed, errors, checks_run, security_blocked}
  ...
}
created_at: float         — Unix timestamp
updated_at: float
```

**`DagNodeExecutionState`** (persisted in `dags/{dag_id}_state.json`):
```
node_id: str
status: str               — PENDING | RUNNING | PASSED_VERIFICATION | FAILED | ESCALATED
attempts: int             — number of verification loop runs for this node
verification_status: str  — maps from VerificationStatus (PASSED, BLOCKED, etc.)
```

**`Event`** (one per line in `logs/events.jsonl`):
```
sessionId: str
id: str                   — UUID
timestamp: float          — Unix ms
location: str             — "module/file.py:function_name"
message: str
runId: str                — task_id or "intent" for compilation events
hypothesisId: str         — agent_id or step name
traceId: str|null         — propagated through entire DAG run
dagId: str|null
data: {arbitrary}         — structured payload (attempt, error, verdict, etc.)
```

**`Episode`** (one per line in `memory/episodes.jsonl`):
```
episode_id: str
task_id: str
dag_id: str
error_text: str
resolution: str
target_file: str
created_at: float
metadata: {retry_count, ...}
```

### Data Flow Summary

```
User intent
    ↓ (POST /api/dags/run)
DagSpec written to dags/{dag_id}.json
    ↓ (execute_dag)
DagNodeExecutionState written/updated to dags/{dag_id}_state.json per node transition
    ↓ (per node, verification loop)
Checkpoint written to checkpoints/{checkpoint_id}.json (pre-state at start)
Checkpoint updated (post-state + diff + tool_logs on verdict)
Events appended to logs/events.jsonl at every significant step
    ↓ (on successful retry)
Episode appended to memory/episodes.jsonl
    ↓ (on user attaching a doc)
TaskContext written to task_context/{task_id}.json
```

### Configuration Knobs (`verification/config.py`)

| Parameter | Default | Effect |
|-----------|---------|--------|
| `max_retries` | 2 | Max coder retry attempts before Supreme Court or BLOCKED |
| `max_error_history_chars` | 4000 | Prune threshold before memory_summarizer_v1 compresses |
| `supreme_court_enabled` | True | Whether to invoke supreme_court_v1 on retry exhaustion |
| `judge_timeout_seconds` | 90.0 | Per-judge-call timeout |

**Environment variables:**

| Variable | Default | Effect |
|----------|---------|--------|
| `AGENTI_HELIX_REPO_ROOT` | current dir | Root for all `.agenti_helix/` persistence |
| `AGENTI_HELIX_BACKEND_TYPE` | default | LLM backend: `"mlx_local"` for on-device inference |
| `AGENTI_HELIX_DISABLE_LOGGING` | unset | Set to `"1"` to suppress event logging |
| `AGENTI_HELIX_SESSION_ID` | `"dev"` | Labels events for session-level filtering |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8001` | Frontend API base |
| `VITE_API_KEY` | unset | Bearer token for authenticated deployments |
| `VITE_INTENT_USE_LLM` | unset | Set to `"true"` to force LLM compile on all pipeline modes |
