# Failure log template

Use this for defects discovered during headless eval runs or manual UI testing.

| failure_id | date | version_tested | what_triggered_the_problem | what_happened | severity | fix_attempted | current_status |
|---|---|---|---|---|---|---|---|
| F-S6-CASCADE-STATE | 2026-04-26 | `c12d7c4` | Run headless eval `--tags all` (scenario `s6_cascade_fail`, fixture resume) | Feature column was `BLOCKED` as expected, but persisted DAG state showed downstream nodes `N2` and `N3` as `PENDING` instead of `FAILED` at assertion time | MED | Proposed fix documented in `demo-repo/eval/fix-report-s5-s6.md`: persist cascade-failed nodes immediately in orchestrator | open |
| F-S5-SECURITY-SHORTCIRCUIT | 2026-04-26 | `c12d7c4` | Run headless eval `--tags all` (scenario `s5_security_shell`) | Scenario expects bandit to short-circuit before judge, but event log contained `Judge evaluated edit` (harness failure) | HIGH | Proposed fix documented in `demo-repo/eval/fix-report-s5-s6.md`: bandit JSON parsing + ensure security block breaks before judge | open |

## Notes

- **Where to look for evidence**: `.agenti_helix/events.jsonl`, `.agenti_helix/checkpoints/`, `demo-repo/.agenti_helix/eval/last-run.json`, and UI screenshots under `screenshots/`.
- **Suggested severity guide**:
  - **CRITICAL**: data loss, security exposure, or system unusable
  - **HIGH**: core workflow broken (cannot run DAG / cannot reach terminal state)
  - **MED**: incorrect state/reporting, confusing UX, intermittent failures
  - **LOW**: cosmetic issues, minor copy/layout problems

