"""
Control-plane API.

This package serves observability artifacts and derived views to the UI:
- `.agenti_helix/dags/*` (DAG specs + execution state)
- `.agenti_helix/checkpoints/*` (Phase 2 checkpoints)
- `.agenti_helix/logs/events.jsonl` (event stream)

The HTTP contract is consumed by the Vite/React frontend.
"""

