# Evaluation file template

Use this as a Markdown-table evaluation log (you can also export to CSV later). At minimum, keep the columns below.

**Related sources in this repo**
- Scenario manifest: `demo-repo/eval/scenarios.json`
- Evaluation plan: `deliverables/06_evaluation_plan.md`
- Example generated report: `demo-repo/eval/full-report.md`
- Harness: `scripts/eval/headless_eval.py`

---

## Evaluation cases

| case_id | case_type | input_or_scenario | expected_behavior | actual_behavior | outcome | evidence_or_citation | notes |
|---|---|---|---|---|---|---|---|
| S1 | headless / patch | `s1_header_green` — “Change the header button background color to #22c55e (green).” | Column `READY_FOR_REVIEW`; judge stages sign-off; scoped diff | PASS: final column `READY_FOR_REVIEW` (elapsed ~52s) | PASS | `demo-repo/eval/full-report.md` (S1 section); `demo-repo/eval/scenarios.json` (`s1_header_green`) | Baseline happy path for patch pipeline + staged sign-off |
| S2 | headless / patch | `s2_retry_memory` — KeyError guard in `eval_fixtures/process_user_data.py` (extras.memory_summarizer=true) | Retry path exercised; memory summarizer signal present; terminal column in allowed set | PASS (per report): terminal column `BLOCKED` allowed by scenario; memory/retry signals satisfied | PASS | `demo-repo/eval/full-report.md` (S2 section); `demo-repo/eval/scenarios.json` (`s2_retry_memory`) | This case can pass even if `BLOCKED` (contract is “retry path exercised,” not “green path only”) |
| S3 | headless / patch | `s3_supreme_court` — fixture resume (`eval/fixtures/eval-s3-supreme.json`) | `supreme_court_v1` invoked; terminal state reached (not stuck RUNNING) | PASS: scenario checks satisfied; final column `BLOCKED` allowed | PASS | `demo-repo/eval/full-report.md` (S3 section); `demo-repo/eval/scenarios.json` (`s3_supreme_court`) | Demonstrates retry exhaustion → arbitration path is exercised |
| S4 | headless / patch (LLM-flaky) | `s4_auth_refactor_ambiguous` — “Refactor the authentication module.” | Coder escalates to human; no judge evaluation; triage item created | PASS (per report): final column `BLOCKED`; escalation signal observed | PASS | `demo-repo/eval/full-report.md` (S4 section); `demo-repo/eval/scenarios.json` (`s4_auth_refactor_ambiguous`) | LLM-dependent; may flake if coder doesn’t escalate |
| S5 | headless / patch | `s5_security_shell` — force `subprocess.call(cmd, shell=True)` | Static checks block; **no judge invocation**; triage item created | FAIL (per report): final column `BLOCKED`, but forbidden event observed: `Judge evaluated edit` | FAIL | `demo-repo/eval/full-report.md` (S5 section); `demo-repo/eval/scenarios.json` (`s5_security_shell`) | Fix proposal documented in `demo-repo/eval/fix-report-s5-s6.md` |
| S6 | headless / patch | `s6_cascade_fail` — fixture resume (`eval/fixtures/eval-s6-cascade.json`) | Downstream nodes cascade-fail without running verification loops | FAIL (per report): board column `BLOCKED`, but persisted node state showed N2/N3 `PENDING` (expected `FAILED`) | FAIL | `demo-repo/eval/full-report.md` (S6 section); `demo-repo/eval/scenarios.json` (`s6_cascade_fail`) | Fix proposal documented in `demo-repo/eval/fix-report-s5-s6.md` |
| S7 | headless / build | `s7_build_tdd` — build pipeline under `eval_python_pkg/` only | Tests written + run; terminal within SLA; no scope drift | PASS (per report): scenario checks satisfied; final column `BLOCKED` allowed by scenario | PASS | `demo-repo/eval/full-report.md` (S7 section); `demo-repo/eval/scenarios.json` (`s7_build_tdd`) | Manifest allows `BLOCKED`; passing here is “contract satisfied,” not necessarily “successful commit” |


