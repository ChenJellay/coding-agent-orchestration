# Role Definitions

Every role, agent, and system component in Agenti-Helix: what it does, what it receives, what it produces.

---

## System Components (Non-Agent)

### `intent_compiler.py` — Compilation Orchestrator
Wraps the LLM intent compiler agent with retry logic and Pydantic validation. Converts a user's macro_intent into a validated DagSpec. Retries up to 2 times on validation failure, feeding error feedback back to the LLM. Raises `ValueError` if all attempts fail.

**Inputs:** `macro_intent: str`, `repo_path: str`, `dag_id: Optional[str]`, `use_llm: bool`
**Outputs:** `DagSpec` (persisted to `.agenti_helix/dags/{dag_id}.json`)

---

### `orchestrator.py` — DAG Executor
Receives a `DagSpec` and executes its nodes in topological order. Tracks node state (PENDING → RUNNING → PASSED_VERIFICATION or FAILED). If a node fails, all downstream dependents are cascade-failed without running. Each node delegates to the verification loop.

**Inputs:** `DagSpec`
**Outputs:** `DagExecutionResult` (per-node final statuses), state file persisted to disk

---

### `verification_loop.py` — Per-Node State Machine
A LangGraph graph with 7 nodes that governs a single `EditTaskSpec` from pre-checkpoint through coder execution, static checks, judge evaluation, retry logic, and escalation. The core execution unit of the system.

**Inputs:** `EditTaskSpec`, `VerificationConfig`, `cancel_token`
**Outputs:** `VerificationState` (final), updated checkpoint on disk

---

### `chain_runtime.py` — Chain DSL Interpreter
Executes a chain specification (JSON with `steps` array). Each step is either a tool call or an agent invocation. Resolves `{"$ref": "key.path"}` bindings from the running context dict. Accumulates outputs into context between steps.

**Inputs:** `chain_spec: Dict`, `initial_context: Dict`, `run_id`, `hypothesis_id`
**Outputs:** Final context dict after all steps complete

---

### `master_orchestrator.py` — Chain Selector
Given an `EditTaskSpec`, selects the appropriate coder chain and judge chain based on `pipeline_mode` ("patch" or "build") or explicit `coder_chain`/`judge_chain` overrides on the task.

**Inputs:** `EditTaskSpec`
**Outputs:** `Dict` (chain spec) for coder; `Dict` (chain spec) for judge

---

### `memory/store.py` — Episodic Memory Store
Append-only JSONL file of `Episode` records. Supports token-overlap (Jaccard similarity) querying — no embeddings, fully in-process. Only indexes episodes when a task retried at least once and ultimately succeeded, so only genuinely instructive resolutions are stored.

**Inputs (write):** `Episode` (error_text, resolution, task_id, dag_id, target_file)
**Outputs (read):** `List[Episode]` ranked by Jaccard similarity to a query string

---

### FastAPI Backend (`api/`)
REST API providing ~20 endpoints for DAG submission, task management, observability, agent inspection, and merge operations. Authenticates via Bearer token (configurable). All DAG execution happens in background threads; API calls return immediately.

---

### React Frontend (`frontend/`)
Single-page app with 7 pages: Dashboard (submit intent), Features Kanban (track DAGs), Feature DAG view (node status), Task Intervention (re-run/abort/guidance), Sign-Off (diff review + merge), Agent Roster (inspect/edit prompts), Repository Context (repo map + rules). Polls API every 2.5–5 seconds.

---

## Agents — Core Pipeline (Wired)

### `intent_compiler_v1` — DAG Architect
Receives the macro_intent and a JSON repo map. Decomposes the request into an ordered list of subtasks, each targeting a specific file, with node IDs, descriptions, acceptance criteria, and a `pipeline_mode` assignment ("patch" or "build").

| | |
|---|---|
| **Inputs** | `macro_intent: str`, `repo_path: str`, `repo_map_json: str` |
| **Output** | `dag_id?: str`, `nodes: [{node_id, description, target_file, acceptance_criteria, pipeline_mode}]`, `edges: [[src, dst]]` |
| **Pipeline** | Entry point |
| **Backend** | Default (configurable) |

---

### `coder_patch_v1` — Single-File Line Patcher
Given a focused repo map, task intent, and the current content of the target file, produces a line-level patch specifying which lines to replace. Can also emit an escalation signal if the task is too ambiguous to resolve autonomously.

| | |
|---|---|
| **Inputs** | `repo_map_json: str`, `intent: str`, `target_file: str`, `target_file_content: str`, optional `compressed_context: str` |
| **Output** | `filePath: str`, `startLine: int`, `endLine: int`, `replacementLines: [str]` — OR — `escalate_to_human: true`, `escalation_reason: str` |
| **Pipeline** | Patch |
| **Backend** | Default |

---

### `judge_v1` — Snippet Evaluator
Compares the original file snippet against the edited snippet against the acceptance criteria. Also receives static check logs. Returns a binary PASS/FAIL verdict with a justification and optional list of problematic line numbers.

| | |
|---|---|
| **Inputs** | `acceptance_criteria: str`, `original_snippet: str`, `edited_snippet: str`, `language: str`, `tool_logs_json: str` |
| **Output** | `verdict: "PASS" \| "FAIL"`, `justification: str`, `problematic_lines: [int]` |
| **Pipeline** | Patch |
| **Backend** | `mlx_local` |

---

### `memory_summarizer_v1` — Error History Compressor
After a coder retry, compresses accumulated error history into a short scratchpad. Prevents unbounded context growth across retries (§4.3). Distils what went wrong and what constraints the coder must respect on the next attempt.

| | |
|---|---|
| **Inputs** | `errors: str`, `previous_patches: str`, `attempt: int` |
| **Output** | `compressed_summary: str`, `key_constraints: [str]` |
| **Pipeline** | Both |
| **Backend** | `mlx_local` |

---

### `supreme_court_v1` — Deadlock Arbitrator
Invoked after all coder retries are exhausted (§4.4). Receives the full failure context — intent, best patch so far, rejection reasons — and attempts a final resolution. If it produces a patch, execution continues through static checks and judge. If it cannot resolve, the node is BLOCKED.

| | |
|---|---|
| **Inputs** | `intent: str`, `best_patch: str`, `rejection_reasons: str`, `error_history: str` |
| **Output** | `resolved: bool`, `reasoning: str`, optional patch fields (`filePath`, `startLine`, `endLine`, `replacementLines`), `compromise_summary?: str` |
| **Pipeline** | Both |
| **Backend** | `mlx_local` |

---

## Agents — Full TDD Pipeline (Wired in "build" mode)

### `context_librarian_v1` — File Scout
Given the task intent and an AST-level repo map, identifies which files and symbols are needed to implement the feature. Returns a list of required files with the specific symbols needed from each.

| | |
|---|---|
| **Inputs** | `intent: str`, `ast_repo_map_json: str` |
| **Output** | `search_strategy: str`, `required_files: [{file_path, required_symbols: [str]}]` |
| **Pipeline** | Build |

---

### `sdet_v1` — Test Writer (TDD First)
Writes test files before implementation. Receives the intent, acceptance criteria, and the content of the files identified by the librarian. Produces a testing strategy and concrete test files that the implementation must pass.

| | |
|---|---|
| **Inputs** | `intent: str`, `acceptance_criteria: str`, `file_contexts_json: str` |
| **Output** | `testing_strategy: str`, `test_files: [{file_path, content}]` |
| **Pipeline** | Build |

---

### `coder_builder_v1` — Multi-File Implementer
Implements the feature across one or more files. Receives file contexts and test files. Produces complete file contents (not line patches) for each file it modifies. Reports if context is missing.

| | |
|---|---|
| **Inputs** | `intent: str`, `acceptance_criteria: str`, `file_contexts_json: str` |
| **Output** | `implementation_logic: str`, `modified_files: [{file_path, content}]`, `missing_context?: str` |
| **Pipeline** | Build |

---

### `security_governor_v1` — Security Auditor
Reviews the set of modified files (and tests) against repository compliance rules. Determines whether the implementation is safe and lists any violations.

| | |
|---|---|
| **Inputs** | `diff_json_str: str`, `repo_rules_text: str` |
| **Output** | `audit_reasoning: str`, `is_safe: bool`, `violations: [str]` |
| **Pipeline** | Build |

---

### `judge_evaluator_v1` — TDD Judge
Evaluates the implementation by examining: test results, acceptance criteria, the diff, and intent. Returns whether tests pass and specific feedback for the coder if they do not.

| | |
|---|---|
| **Inputs** | `intent: str`, `acceptance_criteria: str`, `diff_json_str: str`, `terminal_logs: str` |
| **Output** | `evaluation_reasoning: str`, `pass_tests: bool`, `feedback_for_coder: str` |
| **Pipeline** | Build |

---

### `scribe_v1` — Commit Documenter
Writes a structured commit message and semantic trace log after a task completes successfully. Intended to run post-merge.

| | |
|---|---|
| **Inputs** | Task execution summary (intent, acceptance_criteria, diff summary) |
| **Output** | `summary_reasoning: str`, `commit_message: str` (conventional commits format), `semantic_trace_log: str` |
| **Pipeline** | Build |

---

## Tool-Agent Prompts — New Specialized Agents (Prompts authored; not yet wired into chains)

| Agent | Role | Key Inputs | Key Outputs |
|-------|------|------------|-------------|
| `code_searcher_v1` | Find symbol definitions and call-sites across the repo | `search_query`, `search_type` (symbol/pattern/error/import) | `matches[]` with file_path, line_number, context snippets |
| `linter_v1` | Parse raw linter output into actionable findings | `linter_raw_output`, `acceptance_criteria` | `findings[]` with rule_id, fix_hint, blocks_acceptance |
| `diff_validator_v1` | Validate git diff scope, safety, and rule compliance | `git_diff`, `allowed_paths`, `repo_rules_text` | `verdict` (PASS/WARN/BLOCK), `findings[]`, `out_of_scope_files` |
| `doc_fetcher_v1` | Extract constraints and examples from attached doc URLs | `doc_content`, `intent` | `key_constraints[]`, `code_examples[]`, `task_relevance_summary` |
| `memory_writer_v1` | Distil failure→resolution into a reusable episode | `error_history`, `resolution_summary`, `attempt_count` | `episode` with `error_pattern`, `resolution_pattern`, `anti_patterns`, `should_persist` |
| `type_checker_v1` | Parse mypy/tsc output into concrete fix instructions | `type_checker_output`, `file_content` | `type_health` (clean/fixable/structural), `findings[]` with fix_instruction |
