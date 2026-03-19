## `agenti_helix.orchestration`

Macro-intent → DAG compilation and deterministic DAG execution.

### Responsibilities

- Compile a macro-intent into a small DAG of micro-tasks (LLM-based intent compiler or deterministic fallback).
- Execute nodes only when all predecessors are `PASSED_VERIFICATION`.
- Persist DAG specs and execution state for observability/UI.

### Persistence

- DAG specs/state: `.agenti_helix/dags/*.json`

