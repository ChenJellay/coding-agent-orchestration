from __future__ import annotations

"""
Phase 3 – DAG-based orchestrator.

This package compiles macro-intents into small DAGs of Phase 2 edit tasks and
executes them deterministically.
"""

from .orchestrator import DagExecutionResult, DagNodeExecutionState, DagNodeStatus, DagSpec, execute_dag  # noqa: F401
from .intent_compiler import compile_macro_intent_to_dag  # noqa: F401

