# Screenshot index template

All screenshots should live under `screenshots/`. This index helps reviewers find the right image and understand why it matters.

| screenshot_file | what_it_shows | why_it_matters | where_it_is_discussed_in_the_report |
|---|---|---|---|
| `screenshots/main_screen.png` | Dashboard submission form (repo path + macro intent + pipeline selector) | Shows the primary user entry point and “Run command” workflow | `deliverables/09_track_additions.md` (“Submission”) |
| `screenshots/main_screen_scrolled_down.png` | Dashboard lower area (events/log visibility) | Shows runtime observability while compilation/execution happens in background | `deliverables/09_track_additions.md` (“Compilation (Background)”) |
| `screenshots/feature_kanban.png` | Features Kanban board overview | Evidence for product signal mapping: Scoping/Orchestrating/Blocked/Verifying/Ready for Review | `deliverables/09_track_additions.md` (“Kanban Observation”); `demo-repo/eval/full-report.md` (columns per scenario) |
| `screenshots/feature_kanban_detail.png` | Feature-level detail from Kanban | Supports claims about feature card metadata (confidence/ETA/pass counts) | `deliverables/09_track_additions.md` (“Kanban Observation”) |
| `screenshots/feature_task_level_detail.png` | Task Intervention page (briefing + logs + context injector) | Evidence for “rerun with guidance”, abort, and debugging workflow | `deliverables/09_track_additions.md` (“Task Intervention”) |
| `screenshots/repo_context.png` | Repository Context page (repo map and rules surface) | Evidence for bounded context and repo-map driven scoping | `deliverables/06_evaluation_plan.md` (global criteria: allowed paths, events completeness) |
| `screenshots/agent_prompts.png` | Agent roster / prompt inspection UI | Evidence for governance: ability to inspect/edit prompts and see agent catalog | `deliverables/02_role_definitions.md` (Agent Roster page); `deliverables/08_contribution_update.md` (prompt/schema alignment work) |
| `screenshots/escalation+failure cae.png` | Blocked/escalation/failure state UI | Evidence for management-by-exception: blocked column + triage-style intervention | `deliverables/06_evaluation_plan.md` (S4 escalation; S5 security block; S6 cascade) |

