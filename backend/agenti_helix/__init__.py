"""
Agenti-Helix backend package.

Organized by architectural responsibility:

- core: codebase indexing, AST-aware symbol extraction, patch primitives,
        dependency graph, and repo map generation
- agents: agent registry, Pydantic I/O models, and prompt templates
- runtime: chain/agent execution, inference backends (MLX local + OpenAI),
           tool registry, and chain defaults
- memory: episodic memory store (JSONL-backed) with Jaccard similarity search
- verification: checkpointed verification loop, LangGraph state machine,
                static checks (py_compile, ruff, bandit), Supreme Court node,
                and the local judge FastAPI service
- orchestration: DAG compilation (deterministic + LLM-based with retry),
                 DAG executor, and master orchestrator routing
- api: FastAPI control-plane (authentication, job registry, Git blame endpoint,
       task command routes)
- observability: structured event logging with trace_id / dag_id propagation
- sandbox: (planned) ephemeral Docker sandbox for isolated patch validation
"""
