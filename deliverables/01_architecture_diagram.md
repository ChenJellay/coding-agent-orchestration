# Architecture Diagram

## Agenti-Helix — Multi-Agent Orchestration System

```mermaid
flowchart TD
    subgraph UI["Frontend (React)"]
        DASH["Dashboard\n(macro_intent + pipeline_mode)"]
        KANBAN["Features Kanban\n(SCOPING → READY_FOR_REVIEW)"]
        INTERV["Task Intervention\n(guidance, re-run, abort)"]
        SIGNOFF["Sign-Off / Merge\n(diff review, merge to main)"]
    end

    subgraph API["FastAPI Backend"]
        ROUTE["POST /api/dags/run"]
        BG["start_background_job()\n_compile_and_execute()"]
    end

    subgraph COMPILE["Intent Compilation"]
        IC["intent_compiler_v1\n(DAG Architect)"]
        REPO_MAP["build_repo_map_context()"]
        DAG_PERSIST["DagSpec persisted\n.agenti_helix/dags/{dag_id}.json"]
    end

    subgraph ORCH["DAG Orchestrator (orchestrator.py)"]
        TOPO["topological_order(nodes, edges)"]
        NODE_LOOP["for each node\n(respects dependencies)"]
    end

    subgraph VL["Verification Loop — per node (LangGraph)"]
        PRE["take_pre_checkpoint\n(snapshot target file)"]

        subgraph CODER_CHAIN["Coder Chain"]
            direction LR
            C_PATCH["PATCH: coder_patch_v1\n(line-level diff)"]
            C_BUILD["BUILD: librarian_v1 → sdet_v1\n→ coder_builder_v1\n(write_all_files)"]
        end

        STATIC["run_static_checks\n(syntax · ruff · bandit)"]

        subgraph JUDGE_CHAIN["Judge Chain"]
            direction LR
            J_PATCH["PATCH: judge_v1\n(snippet comparison)"]
            J_BUILD["BUILD: run_tests → governor_v1\n→ judge_evaluator_v1"]
        end

        VERDICT["handle_verdict"]
        SUMM["summarize_context\nmemory_summarizer_v1"]
        SC["supreme_court_v1\n(frontier-model arbitration)"]
    end

    subgraph ESC["Escalation Paths"]
        ESC_HUMAN["BLOCKED — Human Triage\n(coder raised hand)"]
        ESC_SEC["BLOCKED — Security\n(bandit critical finding)"]
        ESC_SC_FAIL["BLOCKED — SC Failed\n(retries exhausted)"]
    end

    subgraph PASS["Resolution"]
        PASS_NODE["PASSED_VERIFICATION\n(checkpoint post-state saved)"]
        MERGE["Merge to main\nPOST /api/tasks/merge"]
    end

    subgraph STORE["Persistence (.agenti_helix/)"]
        S_DAG["dags/{dag_id}.json\n(DagSpec + state)"]
        S_CP["checkpoints/{id}.json\n(pre/post snapshots + diff)"]
        S_MEM["memory/episodes.jsonl\n(episodic store — Jaccard)"]
        S_LOG["logs/events.jsonl\n(structured event stream)"]
        S_RULES["rules.json\n(compliance rules)"]
    end

    %% Entry flow
    DASH -->|"POST /api/dags/run\nmacro_intent + pipeline_mode"| ROUTE
    ROUTE -->|"returns dag_id immediately"| BG
    BG --> REPO_MAP
    REPO_MAP --> IC
    IC -->|"nodes[] + edges[]"| DAG_PERSIST
    DAG_PERSIST --> TOPO

    %% DAG execution
    TOPO --> NODE_LOOP
    NODE_LOOP -->|"EditTaskSpec per node"| PRE

    %% Verification loop
    PRE --> CODER_CHAIN
    CODER_CHAIN --> STATIC
    STATIC -->|"security_blocked=True"| ESC_SEC
    STATIC -->|"passed"| JUDGE_CHAIN
    JUDGE_CHAIN --> VERDICT

    %% Verdict routing
    VERDICT -->|"PASS"| PASS_NODE
    VERDICT -->|"FAIL + retries < max\n(retry ≥ 2: compress context)"| SUMM
    SUMM --> CODER_CHAIN
    VERDICT -->|"FAIL + retries exhausted\nSC enabled"| SC
    SC -->|"resolved"| STATIC
    SC -->|"failed"| ESC_SC_FAIL

    %% Coder escalation
    CODER_CHAIN -->|"escalate_to_human=True"| ESC_HUMAN

    %% After pass
    PASS_NODE -->|"all nodes passed"| KANBAN
    KANBAN --> SIGNOFF
    SIGNOFF --> MERGE

    %% Human intervention
    INTERV -->|"Re-run / Abort / Guidance"| NODE_LOOP

    %% Storage writes
    PRE --> S_CP
    PASS_NODE --> S_CP
    IC --> S_DAG
    NODE_LOOP --> S_DAG
    VERDICT -->|"retry resolved"| S_MEM
    VL --> S_LOG
    STATIC --> S_RULES

    %% UI polling
    KANBAN -.->|"GET /api/features\nevery 5s"| API
    INTERV -.->|"GET /api/events\nGET /api/checkpoints"| API
```

---

## Layer Summary

| Layer | Components | Responsibility |
|-------|-----------|---------------|
| **Entry** | UI Dashboard, `POST /api/dags/run`, `_compile_and_execute()` | Accept user intent; return `dag_id` immediately |
| **Compilation** | `intent_compiler_v1`, `_resolve_target_file()` | Decompose macro_intent → validated DAG of EditTaskSpecs |
| **Orchestration** | `execute_dag()`, topological sort, cascade-fail | Schedule nodes in dependency order; track state |
| **Verification** | LangGraph state machine (7 nodes), coder/judge chains | Apply edits; verify against acceptance criteria; retry |
| **Escalation** | `supreme_court_v1`, human escalation signal, security block | Resolve deadlocks or hand off to human |
| **Memory** | `memory/store.py`, `memory_summarizer_v1` | Learn from retries; compress error history |
| **Persistence** | `.agenti_helix/` file tree | Snapshot state for resumability and observability |
| **Observation** | Events JSONL, SSE stream, `/api/features` | Real-time UI updates; audit trail |
