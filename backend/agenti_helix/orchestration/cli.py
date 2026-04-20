from __future__ import annotations

import argparse
from pathlib import Path

from agenti_helix.orchestration.intent_compiler import compile_macro_intent_to_dag
from agenti_helix.orchestration.orchestrator import execute_dag


def main() -> None:
    parser = argparse.ArgumentParser(description="Agenti-Helix DAG orchestrator demo CLI")
    parser.add_argument(
        "macro_intent",
        type=str,
        nargs="?",
        default=(
            "Update the Header component so that its primary button uses a green "
            "background while preserving existing layout and UX."
        ),
        help="High-level natural language intent for the DAG.",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        default=str(Path("demo-repo").resolve()),
        help="Path to the target repository (default: ./demo-repo)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM-based intent compilation and use deterministic fallback.",
    )
    parser.add_argument(
        "--llm-base-url",
        type=str,
        default=None,
        help="(Deprecated) Base URL for the old intent-compiler HTTP service. Ignored in chain-runtime mode.",
    )
    args = parser.parse_args()

    dag_spec = compile_macro_intent_to_dag(
        args.macro_intent,
        repo_path=args.repo_path,
        dag_id="dag-cli-run",
        use_llm=not args.no_llm,
        llm_base_url=args.llm_base_url,
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

