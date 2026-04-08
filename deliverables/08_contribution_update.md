# Contribution Update

A concise summary of what was designed, built, and fixed across recent working sessions on this project.

---

## Agent Architecture

**Removed duplicate agent.** `dag_generator_v1` was a direct duplicate of `intent_compiler_v1` with a weaker prompt. Its prompt file (`dag_generator_architect.md`) and registry entry were deleted. Any unique sections from the removed prompt were absorbed into `intent_compiler.md`.

**Fixed 7 agent prompt/output schema mismatches.** The build pipeline agents (`coder_builder_v1`, `sdet_v1`, `context_librarian_v1`, `security_governor_v1`, `judge_evaluator_v1`, `scribe_v1`, `memory_summarizer_v1`) all had prompts that specified output formats inconsistent with their Pydantic models. Each prompt was rewritten to output exactly what the model validates — field names, types, and nesting now match the Python definitions.

---

## Orchestrator + Pipeline Mode

**Upgraded `intent_compiler_v1` to full orchestrator.** The intent compiler prompt was rewritten to function as a pipeline-aware orchestrator. It now knows about both execution pipelines (patch and build), all available agents, and assigns `pipeline_mode` per DAG node based on task complexity.

**Added `pipeline_mode` across the stack.** Added `pipeline_mode: str` ("patch" | "build") to:
- `IntentNodeSpec` (Pydantic model in `models.py`)
- `EditTaskSpec` (dataclass in `checkpointing.py`)
- `ExecuteDagFromDashboardRequestBody` (API request body)
- `startDagFromDashboard()` (frontend API client)

**Wired the full TDD build pipeline.** Implemented two new chain functions in `chain_defaults.py`:
- `default_full_pipeline_coder_chain()` — build_ast_context → context_librarian_v1 → load_file_contents → sdet_v1 → coder_builder_v1 → write_all_files
- `default_full_pipeline_judge_chain()` — run_tests → load_rules → security_governor_v1 → judge_evaluator_v1 → map_evaluator_verdict

**Added 6 new tool functions** to `runtime/tools.py` and registered them in `TOOL_REGISTRY`:
- `build_ast_context` — focused AST context for the context librarian
- `load_file_contents` — bulk file reading for build pipeline
- `write_all_files` — writes code + test files to disk
- `run_tests` — executes pytest or jest, returns pass/fail + logs
- `load_rules` — reads `.agenti_helix/rules.json`
- `map_evaluator_verdict` — translates build pipeline judge output into verification loop format

**Built `master_orchestrator.py`** (`resolve_coder_chain` and `resolve_judge_chain`): routes to the correct chain based on `pipeline_mode` or explicit task-level chain overrides.

**Updated verification loop** to pass `intent` and `diff_json` into judge chain context — both required by the build pipeline's judge steps.

---

## Bug Fix: "Hangs on Scheduling"

**Root cause identified and fixed.** When `VITE_INTENT_USE_LLM=true` is set in `frontend/.env.local`, the frontend sends `use_llm=true` for all pipeline modes. The server was previously calling `compile_macro_intent_with_llm()` synchronously in the POST handler, blocking the HTTP response for the entire LLM inference duration (30–120s on MLX). The UI showed "Scheduling…" for the full duration with no feedback.

**Fix:** Moved the entire compile + pipeline_mode assignment + `execute_dag()` call into the `start_background_job` target function. The `POST /api/dags/run` handler now always returns `{"ok": true, "dag_id": "..."}` immediately, regardless of `use_llm`. LLM compile and execution happen in the background thread.

---

## Frontend

**Replaced agent checkbox panel with pipeline selector.** The Dashboard previously showed a list of agent checkboxes that were confusing (agents are not user-selectable in the pipeline model). Replaced with a 3-option radio selector:
- **Quick patch** — `coder_patch_v1 → judge_v1`
- **Full TDD build** — `librarian → sdet → coder_builder → governor → judge_evaluator`
- **Orchestrator decides** — `intent_compiler_v1` assigns pipeline per node (requires LLM)

**Identified 12 UI issues** in a frontend inspection pass, documented in `frontend_changes.md`:
- 2 duplicate action buttons (same handler, different labels)
- 2 duplicate links to the same URL on FeatureCard
- 2 redundant data displays (Column mix repeats stat cards; "What will happen" repeats radio desc)
- Rules.json shown on both Settings and Repository Context pages
- Compute page is a single stat already shown on the Dashboard
- Topbar "Burn: —" and "Profile" are unpopulated placeholders
- Stale scaffold copy in the 404 route
- Two conflicting line-limit controls on the repo map viewer
- "Merge to main" styled identically to navigation pills
- `ErrorBox` component unused; pages reinvent it inline

---

## New Tool-Agent Prompts

**Authored 6 prompt files** for specialized tool-agent roles not yet wired into chains. Each follows the system prompt pattern with `## Role`, `## Context` (template vars), `## Task`, `## Output` (JSON only), and `## Rules`:

| File | Agent | Role |
|------|-------|------|
| `code_searcher.md` | `code_searcher_v1` | Find symbol definitions and call-sites across the repo |
| `linter.md` | `linter_v1` | Parse linter output into structured, actionable findings |
| `diff_validator.md` | `diff_validator_v1` | Validate git diff scope, safety, and rule compliance |
| `doc_fetcher.md` | `doc_fetcher_v1` | Extract constraints from attached doc URLs |
| `memory_writer.md` | `memory_writer_v1` | Distil failure→resolution into reusable episodes |
| `type_checker.md` | `type_checker_v1` | Translate mypy/tsc output into concrete fix instructions |

These agents address gaps in the current pipeline: no codebase search capability, no structured linting feedback, no ground-truth diff scope validation, no doc URL consumption, no explicit memory persistence agent, and no type-checking feedback loop.
