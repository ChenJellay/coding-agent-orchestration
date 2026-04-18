from __future__ import annotations

from typing import Dict, List


PIPELINE_PRESETS: Dict[str, List[str]] = {
    "product_eng": [
        "doc_fetcher_v1",
        "context_librarian_v1",
        "sdet_v1",
        "coder_builder_v1",
        "security_governor_v1",
        "diff_validator_v1",
        "judge_evaluator_v1",
        "scribe_v1",
        "memory_writer_v1",
    ],
    "diff_guard_patch": [
        "coder_patch_v1",
        "diff_validator_v1",
        "judge_v1",
        "scribe_v1",
        "memory_writer_v1",
    ],
    "secure_build_plus": [
        "context_librarian_v1",
        "sdet_v1",
        "coder_builder_v1",
        "security_governor_v1",
        "diff_validator_v1",
        "judge_evaluator_v1",
        "judge_v1",
        "scribe_v1",
        "memory_writer_v1",
    ],
    "lint_type_gate": [
        "context_librarian_v1",
        "sdet_v1",
        "coder_builder_v1",
        "linter_v1",
        "type_checker_v1",
        "judge_evaluator_v1",
        "scribe_v1",
        "memory_writer_v1",
    ],
}


def is_pipeline_preset(name: str | None) -> bool:
    return bool(name and name in PIPELINE_PRESETS)


def get_pipeline_workflow(name: str) -> List[str]:
    return list(PIPELINE_PRESETS[name])
