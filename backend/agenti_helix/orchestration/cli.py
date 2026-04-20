from __future__ import annotations

import argparse
from pathlib import Path

from agenti_helix.orchestration.intent_compiler import compile_macro_intent_to_dag
from agenti_helix.orchestration.orchestrator import execute_dag


def main() -> None:
    parser = argparse.ArgumentParser(description="Agenti-Helix DAG orchestrator CLI")
    parser.add_argument(
        "macro_intent",
        type=str,
        help="High-level natural language intent for the DAG.",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        default=str(Path("demo-repo").resolve()),
        help="Path to the target repository (default: ./demo-repo)",
    )
    args = parser.parse_args()

    dag_spec = compile_macro_intent_to_dag(
        args.macro_intent,
        repo_path=args.repo_path,
        dag_id="dag-cli-run",
        user_intent_label=args.macro_intent.strip(),
    )

    result = execute_dag(dag_spec)
    print(f"DAG {result.dag_id} completed.")
    print("Node statuses:")
    for node_id, state in sorted(result.node_states.items()):
        print(f"  {node_id}: {state.status.value}")
    print(f"All passed: {result.all_passed}")


if __name__ == "__main__":
    main()
