# Risk and Governance Plan

Concrete risks specific to this system's architecture — not generic software risks. Each entry names the mechanism that could fail, the current mitigation, what residual exposure remains, and what governance action addresses it.

---

## Risks

### R1 — LLM Hallucinated File Path in DAG Compilation

**Mechanism:** `intent_compiler_v1` may return a `target_file` path that does not exist in the repository. This happens because the LLM generates paths from the repo map description, not by verifying actual filesystem state.

**Current mitigation:** `_resolve_target_file()` in `intent_compiler.py` performs a 4-stage fuzzy match:
1. Exact path match
2. Case-insensitive full path match
3. Same directory, same stem (any extension)
4. Global stem match (shortest path wins)

**Residual risk:** If no stage matches (truly invented path), the LLM's guess is used verbatim. The coder agent calls `snapshot_target_file()` which will raise `FileNotFoundError` → verification loop catches it as a coder error and retries. Retries waste tokens and time on an unfixable error; after exhaustion the node is BLOCKED.

**Governance action:** Add a validation gate in `compile_macro_intent_with_llm()` after `_resolve_target_file()`: if the resolved path does not exist on disk, reject the node during compilation (raise with clear error) rather than persisting a broken DagSpec. Fail fast at compile time, not at coder time.

---

### R2 — Stale Line Numbers After Rollback

**Mechanism:** When a coder retry occurs, the target file is rolled back to `original_content`. However, the coder may re-use line number assumptions from its `compressed_context` (which summarizes the previous attempt's patch). If the previous patch changed the number of lines, the coder's next patch targets the wrong range.

**Current mitigation:** `tool_snapshot_target_file` is called in the coder chain's first step — the coder sees fresh file content before generating the patch. The chain is: `get_focused_context` → `snapshot_target_file` → `coder_patch_v1`. The snapshot happens correctly on first attempt.

**Residual risk:** On retries, the chain is re-executed from the beginning, so `snapshot_target_file` does re-run. However, the `compressed_context` injected into the coder's intent string may contain specific line numbers from the previous failed patch. If the coder reads those numbers from its scratchpad instead of the fresh snapshot, it will produce an incorrect patch.

**Governance action:** Update `memory_summarizer_v1`'s prompt to explicitly instruct: "Do not include specific line numbers in the compressed summary — only describe the type of change needed." Add a linting rule to `diff_validator_v1` that checks whether the patch's `startLine`/`endLine` are within the bounds of the current file length.

---

### R3 — Supreme Court Introduces Out-of-Scope Changes

**Mechanism:** `supreme_court_v1` operates without explicit scope constraints. When generating a resolution patch, it may touch more code than the original coder was permitted to, or introduce changes that pass the acceptance criteria but break adjacent functionality.

**Current mitigation:** After the SC applies its patch, execution re-enters `run_static_checks` and `call_judge`, which evaluate correctness. The judge verifies acceptance criteria.

**Residual risk:** `judge_v1` only evaluates the acceptance criteria stated in the task. It does not check for regressions in adjacent logic or verify that the SC's patch is narrowly scoped to the intended change.

**Governance action:** Insert `diff_validator_v1` between the SC patch application and `run_static_checks`. The diff validator checks: (1) only `allowed_paths` files are modified; (2) no function signatures or exports are deleted; (3) no repo rules are violated. A `BLOCK` verdict from the diff validator should prevent the SC's patch from proceeding to the judge.

---

### R4 — Episodic Memory Accumulates Incorrect Episodes

**Mechanism:** Episodes are written when `retry_count > 0` AND verdict is `PASS`. However, `judge_v1` can produce false positives — accepting a patch that technically satisfies the narrow acceptance criteria but is subtly incorrect. That incorrect resolution gets indexed and may mislead future coders facing similar errors.

**Current mitigation:** Only indexes after at least one retry (pure first-attempt success generates no episode), reducing noise. No TTL or quality gate currently.

**Residual risk:** Over time, the episode store will accumulate a mix of high-quality and low-quality resolutions with no way to distinguish them. Querying memory returns the most token-similar episode regardless of quality.

**Governance action:**
1. Add `confidence_score: float` to the `Episode` model — derived from the judge's certainty signals (e.g. number of `problematic_lines` in the final PASS verdict; lower is more confident).
2. Add `DELETE /api/memory/episodes/{episode_id}` endpoint for human review and pruning.
3. Add a TTL (e.g. 90 days) after which old episodes are excluded from query results unless explicitly pinned.
4. Store `retry_count_at_resolution` — episodes resolved on retry 3+ (SC involvement) should be flagged for review.

---

### R5 — Unbounded Background Thread Spawning

**Mechanism:** Every `POST /api/dags/run` spawns a new daemon thread via `start_background_job()`. There is no limit on concurrent threads. A burst of submissions can create many threads, each attempting to run MLX inference, which is fundamentally single-threaded on Apple Silicon.

**Current mitigation:** Daemon threads die when the process exits. MLX inference serializes naturally (only one call completes at a time), so threads queue implicitly.

**Residual risk:** Memory growth from many threads each holding large context dictionaries. No visibility into queue depth. UI has no feedback that a submission is queued behind others.

**Governance action:**
1. Replace `threading.Thread` in `start_background_job()` with `concurrent.futures.ThreadPoolExecutor(max_workers=N)` where N is configurable (default: 2 for local, higher for API backend).
2. Return a `queued: true` flag in the `POST /api/dags/run` response if the executor queue is non-empty.
3. Expose a `GET /api/jobs/status` endpoint showing queue depth and active thread count.

---

### R6 — No Security Checks for JavaScript/TypeScript in the Patch Pipeline

**Mechanism:** The patch pipeline's `node_run_static_checks` runs bandit for Python security scanning. For JS/TS files, only syntax checking is performed (`node --check`). A coder could introduce XSS, prototype pollution, `eval()` usage, or insecure `innerHTML` assignment with no security gate.

**Current mitigation:** `security_governor_v1` in the build pipeline performs a security audit. However, the build pipeline is not the default for JS/TS patches — quick patch is.

**Residual risk:** JS/TS code changes in the patch pipeline receive no automated security review before merge.

**Governance action:**
1. Add eslint with the `eslint-plugin-security` ruleset to `node_run_static_checks` for JS/TS targets. Run: `npx eslint --plugin security --rule 'security/detect-eval-with-expression: error' --rule 'security/detect-object-injection: warn' {file}`.
2. Treat `security/detect-*: error` findings as equivalent to bandit HIGH findings → `security_blocked=True`.
3. Add this check to `verification_loop.py:node_run_static_checks` alongside the existing Python-specific path.

---

### R7 — Intent Editing Does Not Trigger Re-Execution

**Mechanism:** The UI allows editing the `macro_intent` of an existing DAG via `PUT /api/dags/{id}/intent`. This updates the DagSpec persisted on disk but does not automatically re-run any nodes. If a user edits the intent mid-run, the running nodes continue with the old intent.

**Current mitigation:** None — the edit is applied to the spec file only.

**Residual risk:** Silent divergence between the stated intent and what the agents are executing. A user who edits the intent to correct a misunderstanding will not see their correction take effect until nodes are manually re-run.

**Governance action:**
1. After a successful intent edit, automatically trigger re-runs for any nodes currently in `PENDING` or `BLOCKED` state (not `RUNNING` — don't interrupt active work).
2. Add a `intent_version` field to `DagSpec`; increment on edit. Nodes that ran on an older version are flagged in the UI as "ran with outdated intent."

---

## Governance Framework

### Compliance Rules (`rules.json`)

Repository owners can place `.agenti_helix/rules.json` in their repo to encode project-specific constraints. The file is read by `tool_load_rules()` and passed to `security_governor_v1` and (once wired) `diff_validator_v1`. Example rule categories:

- **Banned imports:** libraries that may not be introduced (e.g. `os.system`, `eval`)
- **Restricted files:** paths the coder must never touch (e.g. `*.env`, `secrets/*`)
- **Required patterns:** boilerplate that must exist in new files (e.g. license headers)
- **Style constraints:** naming conventions, max function length

### Human Review Gates

The following outcomes always require human review before the result is accepted:

| Outcome | Required Human Action |
|---------|-----------------------|
| `BLOCKED` (any reason) | Review Triage Inbox; provide guidance or close as won't-fix |
| `supreme_court_invoked=True` + PASS | Review Sign-Off diff for scope correctness before merging |
| `human_escalation_requested=True` | Clarify intent, attach doc URL, then re-run with guidance |
| Multi-file change in patch pipeline | Flag for diff review — patch pipeline was designed for single-file changes |

### Merge Authorization

No automated merge is performed without an explicit `POST /api/tasks/merge` call from an authenticated user (role: `editor` or higher). The verification that a checkpoint reached `PASSED` is checked server-side before the merge is executed. The system never auto-merges on PASS alone.

### Audit Trail

Every action — coder output, judge verdict, escalation signal, human re-run, merge — is logged to `events.jsonl` with a stable `trace_id`. This provides a full lineage from macro_intent submission to final merge. Logs are append-only and are not deleted by normal operation.
