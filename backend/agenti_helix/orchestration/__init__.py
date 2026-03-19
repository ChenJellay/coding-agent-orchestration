"""
DAG compilation and deterministic execution.

Responsibilities:
- Compile macro-intents into a small task DAG (LLM-based or deterministic fallback)
- Execute DAG nodes in dependency order
- Persist DAG specs and execution state under `.agenti_helix/dags`

Nodes are executed via the `agenti_helix.verification` loop.
"""

from .intent_compiler import compile_macro_intent_to_dag  # noqa: F401
from .orchestrator import (  # noqa: F401
    DagExecutionResult,
    DagNodeExecutionState,
    DagNodeStatus,
    DagSpec,
    execute_dag,
)

