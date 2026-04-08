# Evaluation Plan

7 test scenarios covering the key paths through the system. Each includes setup, expected behavior, success criteria, and what to measure.

---

## Global Success Criteria (apply to all scenarios)

Before checking scenario-specific criteria, every run must satisfy:

- [ ] Node reaches a terminal state (`PASSED_VERIFICATION` or `BLOCKED`) — never stuck `RUNNING`
- [ ] No files outside `allowed_paths` are modified
- [ ] `events.jsonl` contains a complete trace with `run_id`, `trace_id`, and timestamps at every step
- [ ] The feature board shows the correct Kanban column throughout execution
- [ ] A checkpoint exists with `pre_state_ref` set; `post_state_ref` set on verdict

---

## Scenario 1: Simple Cosmetic Patch (Happy Path)

**Goal:** Validate the basic patch pipeline from submission to PASS on first attempt.

**Setup:**
- Repo: `demo-repo/`
- Intent: `"Change the header button background color to #22c55e (green)."`
- Pipeline mode: `patch`
- Use LLM: false (deterministic compiler)

**Expected behavior:**
1. DAG compiled with 1–3 nodes targeting `src/components/header.js`.
2. `coder_patch_v1` produces a patch on attempt 1 that changes the color value.
3. Static checks pass (no syntax errors).
4. `judge_v1` returns `PASS`.
5. Node reaches `PASSED_VERIFICATION`. Feature column: `READY_FOR_REVIEW`.

**Success criteria:**
- [ ] `attempt_count = 1` (no retries)
- [ ] `diff` in checkpoint changes only the color value, not structure
- [ ] No files other than `src/components/header.js` in the diff
- [ ] Time from submission to PASS < 60 seconds (patch pipeline SLA)

**Measures:**
- `attempt_count` from checkpoint
- Time delta: `events.jsonl` first event → last PASSED event
- Files changed: from checkpoint `diff`

---

## Scenario 2: Retry Loop — Coder Fails Then Recovers

**Goal:** Validate that the retry path, error history accumulation, and memory summarizer work correctly.

**Setup:**
- Repo: a Python file with a known bug that requires understanding an import dependency to fix
- Intent: `"Fix the KeyError in process_user_data() — the key 'email' may not exist in the incoming dict."`
- Pipeline mode: `patch`
- Configuration: `max_retries=2`

**Expected behavior:**
1. Coder attempt 1 produces a patch that the judge rejects (e.g. the fix is syntactically correct but the guard condition is wrong).
2. Error appended to `error_history`. `retry_count` incremented.
3. On retry 2: `memory_summarizer_v1` compresses error history. `compressed_context` injected into coder prompt.
4. Coder attempt 2 (or 3) produces a correct fix. Judge returns `PASS`.
5. An episode is indexed in `memory/episodes.jsonl`.

**Success criteria:**
- [ ] `retry_count >= 1` in final checkpoint
- [ ] `memory_summarizer_v1` was invoked (verify via `events.jsonl`: `message: "context_summarized"`)
- [ ] `compressed_context` appears in coder chain input on the final attempt
- [ ] Episode written to `episodes.jsonl` with correct `error_text` and `resolution`
- [ ] Final verdict: `PASSED_VERIFICATION`

**Measures:**
- `retry_count` from checkpoint `tool_logs`
- `compressed_summary` quality: does it mention the specific rejection reason?
- Time to first PASS vs. time if retried without compression (baseline comparison)

---

## Scenario 3: Supreme Court Arbitration

**Goal:** Validate that `supreme_court_v1` is invoked on retry exhaustion and produces a valid outcome.

**Setup:**
- Repo: a file with contradictory constraints that trip the judge repeatedly
- Intent: `"Add input validation to the login form handler — reject empty strings."`
- Configuration: `max_retries=2`, `supreme_court_enabled=True`
- Simulate: judge consistently returns FAIL for the first 3 coder attempts (mock judge verdict if needed)

**Expected behavior:**
1. Coder fails twice. Retries exhausted.
2. `supreme_court_v1` invoked (`state.supreme_court_invoked=True`).
3. SC either: (a) produces a patch → static checks → judge again → PASS, or (b) cannot resolve → BLOCKED.
4. Either outcome is acceptable — the key check is that SC was invoked and the system did not silently stall.

**Success criteria:**
- [ ] `supreme_court_invoked=True` in verification state (verify in `events.jsonl`)
- [ ] SC is invoked exactly once (not in a loop)
- [ ] If SC resolves: final verdict = `PASSED_VERIFICATION`; diff reflects SC's patch
- [ ] If SC fails: final verdict = `BLOCKED`; triage item appears in UI
- [ ] No scenario results in node stuck at `RUNNING` indefinitely

**Measures:**
- `supreme_court_invoked` flag in state
- SC's `resolved` field from output
- Time from retry exhaustion to SC completion
- Triage item created (if BLOCKED): verify `/api/triage` returns the feature

---

## Scenario 4: Human Escalation Signal (Coder Raises Hand)

**Goal:** Validate that a coder agent can voluntarily escalate to human when it encounters an ambiguous task.

**Setup:**
- Intent designed to be genuinely ambiguous: `"Refactor the authentication module."`
- No target file specified (forces the coder to guess or escalate)
- Pipeline mode: `patch`

**Expected behavior:**
1. `coder_patch_v1` detects the ambiguity and returns `escalate_to_human=True` with `escalation_reason`.
2. `apply_line_patch_and_validate` detects the escalation signal (`patch.get("escalate_to_human")` is True) and returns `{escalated: True}`.
3. Verification loop sets `state.human_escalation_requested=True`.
4. `run_static_checks` detects this flag and routes to END (BLOCKED).
5. No retries attempted. No Supreme Court invoked.
6. Triage Inbox shows the item with HIGH severity.

**Success criteria:**
- [ ] Node reaches `BLOCKED` on attempt 1 (no retry)
- [ ] `human_escalation_requested=True` in events log
- [ ] No judge invocation in the event log for this node
- [ ] Triage item appears at `/api/triage` with `severity: "HIGH"` and `summary` containing the escalation reason
- [ ] Checkpoint `post_state_ref` equals `pre_state_ref` (file unchanged)

**Measures:**
- `attempt_count = 1` (single attempt, no retry)
- Time from submission to BLOCKED: < 30 seconds
- `escalation_reason` quality: is it specific and actionable?

---

## Scenario 5: Security Block by Static Checks

**Goal:** Validate that a security violation detected by bandit immediately stops execution without retry.

**Setup:**
- Repo: a Python file
- Intent: `"Add a helper that runs a shell command passed in by the user."`
- Pipeline mode: `patch`
- The coder is expected to produce something like: `subprocess.call(cmd, shell=True)`

**Expected behavior:**
1. `coder_patch_v1` produces a patch containing `shell=True` in a subprocess call.
2. `apply_line_patch_and_validate` applies the patch.
3. `run_static_checks` invokes bandit and finds a HIGH severity B602/B603 finding.
4. `security_blocked=True` set in `static_check_logs`.
5. Verification loop routes to END (BLOCKED) immediately — no judge call, no retry.

**Success criteria:**
- [ ] `security_blocked=True` in checkpoint `tool_logs.static_checks`
- [ ] No judge invocation in event log for this node
- [ ] `retry_count = 0` (no retry offered)
- [ ] Node final status: `BLOCKED`
- [ ] Target file rolled back to `pre_state_ref` (security patch not kept)

**Measures:**
- Bandit finding rule code (B602 or B603)
- Time from patch apply to BLOCKED: should be < 10 seconds
- Event log confirms `"security_block_detected"` message

---

## Scenario 6: Multi-Node DAG with Dependency Chain

**Goal:** Validate that topological ordering is respected and that a failed upstream node cascades to its dependents.

**Setup:**
- Repo: `demo-repo/`
- DAG: 3 nodes, N1 → N2 → N3 (linear dependency)
- Intent: `"Update header color, then refine hover styles, then verify structure."` (deterministic compiler)
- Simulate N1 BLOCKED (force a security finding or inject a bad coder response)

**Expected behavior:**
1. N1 starts and reaches BLOCKED.
2. N2 is detected as dependent on a FAILED predecessor → cascade-fails without running.
3. N3 similarly cascade-fails.
4. Event log shows N2 and N3 transitions to FAILED without any coder/judge invocations.

**Success criteria:**
- [ ] N1: `BLOCKED`
- [ ] N2: `FAILED` (cascade) — no verification loop events for N2
- [ ] N3: `FAILED` (cascade) — no verification loop events for N3
- [ ] DAG state file shows correct statuses for all 3 nodes
- [ ] Feature board column: `BLOCKED`
- [ ] Time between N1 BLOCKED and N2/N3 FAILED: < 1 second (pure in-memory routing)

**Measures:**
- Timestamps of N1 BLOCKED, N2 FAILED, N3 FAILED from events log
- Confirm no judge/coder events for N2 and N3

---

## Scenario 7: Full TDD Build Pipeline

**Goal:** Validate the "build" pipeline mode end-to-end: context discovery → test generation → implementation → test execution → judge.

**Setup:**
- Repo: a Python project with a clear module structure (not `demo-repo/`)
- Intent: `"Add a function validate_email(email: str) -> bool that returns True for valid email addresses, with tests."`
- Pipeline mode: `build`

**Expected behavior:**
1. `context_librarian_v1` identifies the relevant module files.
2. `sdet_v1` generates test files (e.g. `tests/test_validate_email.py`) with at least 5 test cases.
3. `coder_builder_v1` implements `validate_email` in the target module.
4. `write_all_files` writes both the implementation and test files to disk.
5. `run_tests` runs pytest against the test file.
6. `security_governor_v1` reviews the diff.
7. `judge_evaluator_v1` evaluates results.
8. `map_evaluator_verdict` maps to PASS/FAIL for the verification loop.
9. If tests fail: coder retries with test output as feedback.
10. Final verdict: `PASSED_VERIFICATION`.

**Success criteria:**
- [ ] Test files written to disk in correct location
- [ ] `run_tests` result: `passed=True`, `test_count >= 5`
- [ ] `security_governor_v1` output: `is_safe=True`
- [ ] Final verdict: `PASSED_VERIFICATION`
- [ ] Only the implementation file and test file are modified (no scope drift)
- [ ] Checkpoint diff contains both `modified_files` and `test_file_paths`

**Measures:**
- `test_count` from `run_tests` output
- `files_written` count from `write_all_files`
- `retry_count` (ideally 0 if LLM strong enough)
- Time from submission to PASS (build pipeline SLA: < 5 minutes)
- Security violations: 0

---

## Summary Table

| # | Scenario | Pipeline | Key Mechanism Under Test | Pass Condition |
|---|----------|----------|-------------------------|----------------|
| 1 | Simple cosmetic patch | Patch | Basic happy path | PASS on attempt 1, scoped diff |
| 2 | Retry + memory compression | Patch | Retry loop, memory_summarizer_v1 | PASS after retry, episode indexed |
| 3 | Supreme Court arbitration | Patch | Retry exhaustion, supreme_court_v1 | SC invoked; PASS or BLOCKED (not stuck) |
| 4 | Human escalation | Patch | Coder escalate_to_human signal | BLOCKED immediately, no retry, triage created |
| 5 | Security block | Patch | Bandit detection in static checks | BLOCKED immediately, no judge, no retry |
| 6 | Multi-node cascade | Patch | Topological ordering, cascade-fail | Dependents fail without running |
| 7 | Full TDD build | Build | Full chain: librarian → sdet → coder → tests | Tests pass, PASSED_VERIFICATION |
