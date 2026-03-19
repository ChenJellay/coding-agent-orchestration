from __future__ import annotations

import argparse
from pathlib import Path

from .checkpointing import EditTaskSpec
from .verification_loop import run_verification_loop


def run_demo_task() -> None:
    repo_path = str(Path("demo-repo").resolve())
    task = EditTaskSpec(
        task_id="demo-header-color",
        intent="Change the header button color in src/components/header.js to green.",
        target_file="src/components/header.js",
        acceptance_criteria=(
            "There is exactly one visible button in Header. "
            "Its background color should be green. "
            "No unrelated code should be modified."
        ),
        repo_path=repo_path,
    )

    final_state = run_verification_loop(task)
    status = final_state.checkpoint.status if final_state.checkpoint else None
    print(f"Final status: {status}")
    if final_state.judge_response is not None:
        print("Judge response:")
        print(final_state.judge_response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agenti-Helix verification loop demo CLI")
    parser.parse_args()
    run_demo_task()


if __name__ == "__main__":
    main()

