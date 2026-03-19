## `agenti_helix.observability`

Structured event logging utilities.

Today this is a JSONL append-only logger used by:

- `agenti_helix.verification` (verification loop + judge lifecycle events)
- `agenti_helix.orchestration` (DAG execution events)

Longer-term, this folder is where semantic tracing and audit primitives should live.

