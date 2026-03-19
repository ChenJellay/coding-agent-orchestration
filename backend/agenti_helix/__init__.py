"""
Agenti-Helix backend package.

This package is organized by architectural responsibility (not “phases”):

- core: codebase indexing + AST-aware symbol extraction + patch primitives
- single_agent: single-file edit primitive (local model prompting + patch apply)
- verification: checkpointed verification loop and local judge services/clients
- orchestration: DAG compilation + deterministic execution semantics
- api: FastAPI control-plane API serving `.agenti_helix/*` artifacts to the UI
- observability: structured event logging
"""

