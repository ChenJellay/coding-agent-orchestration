# AI usage log (high-level, by code area)

Use this log to describe **where AI tools contributed** (architecture, debugging, fixes, docs) and what was verified. If you used additional models (e.g. Claude), add rows with the **real prompts** you used.

| Tool name and version | What you used it for (high-level) | Codebase area(s) impacted | Example tasks / prompts (representative) | What you changed manually afterward | What you verified independently |
|---|---|---|---|---|---|
| Cursor Agent (model: GPT-5.2) | **Architecture + documentation shaping** (submission-ready README, project structure explanation) | `README.md`, `scripts/start-dev.sh` (reference), `deliverables/*` (citations) | “Restructure README to minimum submission sections; link to existing deliverables; correct manual run commands to match start script.” | Minor wording + command-line corrections to align with launcher semantics | Read `scripts/start-dev.sh` to confirm ports/commands; ensured README sections match submission checklist |
| Cursor Agent (model: GPT-5.2) | **Evaluation artifacts authoring** (templates + cross-referenced evidence pointers) | `EVALUATION_TEMPLATE.md`, `FAILURE_LOG_TEMPLATE.md`, `SCREENSHOT_INDEX_TEMPLATE.md`, `AI_USAGE_LOG_TEMPLATE.md` | “Create evaluation/failure/screenshot/AI usage templates at repo root and prefill with scenario IDs, outcomes, and citations.” | Adjusted rows to match repo’s scenario IDs and screenshot filenames | Cross-checked against `demo-repo/eval/scenarios.json`, `demo-repo/eval/full-report.md`, `demo-repo/eval/fix-report-s5-s6.md` |
| Headless eval harness (repo script: `scripts/eval/headless_eval.py`) | **Automated evaluation execution** (scenario batch runs) | `scripts/eval/*`, `demo-repo/eval/*`, generated `.agenti_helix/*` artifacts (runtime) | “Run stable/all scenario batches; capture `last-run.json` and summary markdown.” | N/A (script-driven) | Verified outputs and interpretation in `demo-repo/eval/full-report.md` and evaluation plan `deliverables/06_evaluation_plan.md` |
| Local judge service (FastAPI app) | **Verification/judging** (PASS/FAIL verdicts for patch/build pipelines) | `backend/agenti_helix/verification/*` (runtime), consumed by orchestration | “Judge staged sign-off on patch pipeline; ensure security blocks short-circuit (S5 contract).” | N/A (service) | Verified expected vs observed behavior through scenario expectations + report outcomes (e.g. S1 PASS; S5 reported FAIL due to judge event) |
| Claude (model/version: ________) | **(Fill in if used)** architecture / debugging / refactors | e.g. `backend/*` / `frontend/*` / `deliverables/*` | Paste the exact prompts/tasks you actually ran | What you changed manually after Claude output | What you verified (tests, lint, manual run, diff review) |

## Notes

- Keep entries **honest and reproducible**: if a tool wasn’t used, leave it blank or keep it as a placeholder row.
- If you ran multiple Claude sessions, add one row per theme (e.g., “orchestration debugging”, “frontend UX refactor”, “security hardening”) and specify impacted paths.

