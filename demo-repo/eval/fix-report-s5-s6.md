# Fix report: S5 + S6 headless eval failures

**Inputs:**
- Failing eval output: `demo-repo/.agenti_helix/eval/last-run.json`
- Failures: `s6_cascade_fail`, `s5_security_shell`

**Goal:** Make these scenarios pass deterministically by fixing product behavior (not watering down assertions).

---

## 1. Failure recap (from `last-run.json`)

### S6 — `s6_cascade_fail` (cascade)

Observed errors:
- `state node 'N2': status got 'PENDING', want 'FAILED'`
- `state node 'N3': status got 'PENDING', want 'FAILED'`

Meaning: downstream nodes were **cascade-failed logically**, but the persisted DAG state still showed them as **PENDING** at the time the eval asserted.

### S5 — `s5_security_shell` (security short-circuit)

Observed error:
- `forbidden event message appeared: 'Judge evaluated edit'`

Meaning: the eval expects **bandit security findings to short-circuit before judge**, but the run produced a judge event—implying `security_blocked` was not reliably detected (or not used as a hard gate).

---

## 2. Changes made

### 2.1 S6 fix: persist cascade-failed node state immediately

File: `backend/agenti_helix/orchestration/orchestrator.py`

Change: when a node is set to `FAILED` due to a failed predecessor, we now call:

- `persist_dag_execution_state(spec.dag_id, node_states)`

*before* continuing the DAG loop.

Why this fixes S6:
- Previously, the orchestrator updated `node_states` in memory but did **not** persist `*_state.json` for these cascade transitions (persistence happened mainly around RUNNING → finished transitions).
- The headless harness reads state via `/api/dags/{dag_id}/state` (or `/api/features/{dag_id}` fallback). Persisting immediately ensures the eval sees **N2/N3 = FAILED** rather than stale **PENDING**.

### 2.2 S5 fix: make bandit detection robust (JSON parsing)

File: `backend/agenti_helix/verification/verification_loop.py`

Change: `_check_bandit_security()` now:
- Runs `bandit ... -f json` (instead of `txt`)
- Parses `results[]` and returns findings only for **HIGH severity + HIGH confidence**
- Emits stable `[SECURITY] ...` error strings
- Includes a defensive fallback that coerces `stdout`/`stderr` to `str` (keeps unit tests stable with mocks)

Why this fixes S5:
- The security short-circuit path depends on `_run_static_checks()` receiving non-empty security errors so it can set `security_blocked=True`.
- The prior implementation depended on specific text output line prefixes (`Issue:` / `>> Issue:`), which can vary across bandit versions and flags (leading to “false clean” scans).
- With JSON parsing, the presence of a B602/B603-style result is reliably detected, and the verification loop takes `_record_security_blocked()` and **breaks before calling the judge**, preventing `Judge evaluated edit` from appearing in `events.jsonl`.

---

## 3. Capability check (does the repo already support this?)

- **Cascade state persistence:** The repo already had `persist_dag_execution_state(...)` and state JSON plumbing. The missing piece was calling it at the cascade transition point.
- **Security short-circuit:** The repo already had `security_blocked` propagation and a hard short-circuit in `run_verification_loop()` when `logs.security_blocked` is true. The missing piece was robust detection/parsing of bandit findings.

---

## 4. Tests added / updated and results

New regression tests:
- `backend/tests/unit/test_eval_regressions.py`
  - Asserts cascade-failed nodes are persisted as `FAILED`
  - Asserts bandit JSON findings produce `[SECURITY] ...` errors

Test run:
- `python -m pytest backend/tests/unit` → **253 passed**

---

## 5. What to re-run to confirm green evals

Once the control plane is up (`:8001`) and Judge is up (`:8000`), re-run:

```bash
python scripts/eval/headless_eval.py --scenario s6_cascade_fail
python scripts/eval/headless_eval.py --scenario s5_security_shell
python scripts/eval/headless_eval.py --tags all
```

Expected:
- S6: `state_nodes` assertions pass (N2/N3 show `FAILED`) and `verification_loop_max_by_node` still holds.
- S5: no `Judge evaluated edit` event for that `dag_id` (security blocks before judge).

