# Track-Specific Additions

---

## 1. Working Prototype of the Interaction Flow

The system is a running web application. The following is a concrete walkthrough of the interaction flow from the user's perspective, with every branch point identified.

### Submission

The user opens the Dashboard at `http://localhost:5173`. Two inputs are required:
- **Local repository path** — a path on the server's filesystem (e.g. `../demo-repo`). A datalist provides presets.
- **Command / macro intent** — free-text natural language description of the desired change.

A pipeline selector (radio) determines execution strategy:
- **Quick patch** — single-file line-level edits. Fastest. No tests written.
- **Full TDD build** — multi-file changes with generated tests. Slower. More thorough.
- **Orchestrator decides** — the LLM intent compiler assigns pipeline mode per subtask based on complexity.

Pressing **Submit command** (or ⌘/Ctrl+Enter) sends `POST /api/dags/run` and returns a `dag_id` within ~1 second regardless of pipeline mode. The button returns to its idle state immediately.

### Compilation (Background)

Invisible to the user unless they watch the Events log. The intent compiler runs in a background thread:
- Queries the repo map
- Calls `intent_compiler_v1` to produce a DAG of subtasks
- Each subtask gets a `target_file`, `acceptance_criteria`, and `pipeline_mode`
- The DAG is persisted and becomes visible on the Features Kanban

**Branch: Compilation failure.** If the LLM returns invalid JSON or an empty nodes list after 2 attempts, the background thread logs an error and exits. The `dag_id` never appears in the feature board. The user sees nothing — currently no failure notification is surfaced to the UI (a known gap, tracked in R5 of the risk plan).

### Kanban Observation

The user navigates to **Features**. The Kanban shows 5 columns: Scoping → Orchestrating → Blocked → Verifying → Ready for Review. The feature card appears initially in **Orchestrating** (DAG compiled, nodes running). Cards show confidence score, ETA, and pass/fail node counts.

**Branch: Node blocked.** If any node reaches BLOCKED state (security violation, escalation, or retry exhaustion), the feature card moves to the **Blocked** column. A Triage Inbox item is created.

### DAG Detail View

Clicking a feature card opens the DAG page (`/features/{id}`). Node pills are displayed with color:
- Gray: PENDING (waiting for predecessor)
- Yellow: RUNNING (verification loop active)
- Green: PASSED_VERIFICATION
- Red: BLOCKED or FAILED

Clicking a node pill navigates to **Task Intervention**.

### Task Intervention

The Task Intervention page shows a three-panel layout:
- **Left — Agent briefing:** Judge's justification from the latest checkpoint. Last checkpoint ID and status.
- **Center — Execution logs:** Every step with timestamps and locations, most recent 80 events.
- **Right — Context injector:** Guidance textarea + optional doc URL input.

**Branch: Re-run with guidance.** User types corrective guidance and clicks **Apply + re-run**. This calls `POST /api/tasks/apply-and-rerun` with the guidance text. The verification loop restarts from the checkpoint; guidance is injected as `compressed_context`. Retry count resets.

**Branch: Abort.** User clicks **Abort task** in the page header. Calls `POST /api/tasks/abort`. The cancel token fires; the active chain step exits at the next cancellation check. Checkpoint recorded as BLOCKED.

**Branch: Attach doc.** User pastes a URL (API spec, PRD, design doc) into the doc link field. This is persisted server-side as task context. On the next re-run, `doc_fetcher_v1` (once wired) will fetch and summarize the doc for the coder.

### Sign-Off

Once all nodes are green, the feature moves to **Ready for Review**. Clicking **Review & Merge** opens the Sign-Off page (`/features/{id}/signoff`).

Three-panel layout:
- **Left — Original intent:** The macro_intent and acceptance criteria. Can be edited (Edit intent button → save in-place).
- **Center — Semantic trace:** The event log for the feature run, showing every agent step.
- **Right — Verified execution:** The latest checkpoint diff — pre-state and post-state file contents side by side.

**Branch: Intent edit.** User edits the macro_intent and saves. The DagSpec is updated on disk. Nodes that ran on the old intent are not automatically re-run (known limitation).

**Branch: View episodic memory.** Clicking "View episodic memory" fetches the memory store's summary for this run and shows it as a collapsible section. Useful for understanding what the agents learned during retries.

**Branch: Merge.** User clicks **Merge to main**. Calls `POST /api/tasks/merge` with the latest checkpoint ID. The server verifies the checkpoint is PASSED, then commits the post-state content to the target branch. Returns `{ok: true, mergeRef: ...}`.

---

## 2. Branch Logic and Workflow Logic

The system has two levels of branch logic: at the DAG level (which nodes run) and at the verification loop level (what happens to a single node).

### DAG-Level Branch Logic

```
For each node in topological order:

  IF any predecessor has status FAILED or BLOCKED:
    → mark this node FAILED (cascade)
    → do not invoke verification loop
    → continue to next node

  ELSE:
    → invoke run_verification_loop(node.task)
    → on return: map VerificationStatus to DagNodeStatus
      PASSED → PASSED_VERIFICATION
      BLOCKED → FAILED (or ESCALATED if human_escalation_requested)
```

The topological sort (Kahn's algorithm) ensures no node runs before its dependencies are resolved. Cycles raise an exception at DAG compile time.

### Verification Loop — Branch Logic

Every routing decision is a deterministic function of `VerificationState` fields:

```
After run_coder:
  state.human_escalation_requested = True
    → route to END [BLOCKED — human raised hand]
  state.coder_error is not None
    → treat as FAIL verdict → route to handle_verdict
  else
    → route to run_static_checks

After run_static_checks:
  state.human_escalation_requested = True
    → route to END [BLOCKED]
  state.static_check_logs.security_blocked = True
    → route to END [BLOCKED — security violation]
  else
    → route to call_judge

After call_judge:
  → always route to handle_verdict

After handle_verdict:
  judge_response.verdict = "PASS"
    → record PASSED checkpoint
    → index memory episode (if retry_count > 0)
    → route to END [PASSED_VERIFICATION]

  judge_response.verdict = "FAIL" AND retry_count < max_retries:
    → append to error_history
    → rollback target file
    → increment retry_count
    → IF retry_count >= 2: route to summarize_context
    → ELSE: route to run_coder

  judge_response.verdict = "FAIL" AND retry_count >= max_retries:
    → IF supreme_court_enabled AND NOT supreme_court_invoked:
        → route to supreme_court
    → ELSE:
        → record BLOCKED checkpoint
        → route to END [BLOCKED]

After supreme_court:
  state.supreme_court_output.resolved = True:
    → apply SC patch to file
    → route to run_static_checks (re-validate from scratch)
  state.supreme_court_output.resolved = False:
    → record BLOCKED checkpoint
    → route to END [BLOCKED]
```

### Coder Chain — Branch Logic (Patch Mode)

```
Step 1: get_focused_context(target_file, depth=1)
  → Returns repo_map_json + allowed_paths

Step 2: snapshot_target_file(target_file)
  → Returns target_file_content (current on-disk state)

Step 3: coder_patch_v1(repo_map_json, intent, target_file_content, ...)
  → Returns either:
    A. {filePath, startLine, endLine, replacementLines}  → continue
    B. {escalate_to_human: true, escalation_reason}       → continue (escalation detected next step)

Step 4: apply_line_patch_and_validate(patch, allowed_paths)
  → If patch.escalate_to_human: return {escalated: True}
  → Else: apply patch, run syntax check, return validated diff
```

### Judge Chain — Branch Logic (Patch Mode)

```
Step 1: snapshot_target_file(target_file)
  → Returns edited_snippet (post-coder file content)

Step 2: infer_language_from_target_file(target_file)
  → Returns language string

Step 3: build_tool_logs_json(static_check_logs)
  → Returns serialized tool logs for judge context

Step 4: judge_v1(acceptance_criteria, original_snippet, edited_snippet, language, tool_logs_json)
  → Returns {verdict: "PASS"|"FAIL", justification, problematic_lines}
```

---

## 3. How the Design Operationalizes Course Concepts

> **[SECTION LEFT BLANK — course concepts were not provided. Please fill in with the relevant framework, theory, or design principles from the course that this system is meant to demonstrate or apply.]**

Suggested subsections once course concepts are available:
- Which concept(s) each architectural decision maps to
- How the agent roles relate to the course's model of autonomous systems
- How the evaluation plan connects to course assessment criteria
- How the risk/governance section relates to responsible AI or system safety frameworks discussed in the course
