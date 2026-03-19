from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def load_prompt_template(filename: str) -> str:
    path = (_prompts_dir() / filename).resolve()
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, variables: Dict[str, Any]) -> str:
    try:
        return template.format(**variables).strip()
    except KeyError as e:
        missing = str(e).strip("'")
        raise KeyError(f"Missing prompt template variable: {missing}") from e


def render_judge_variables(
    *,
    acceptance_criteria: str,
    original_snippet: str,
    edited_snippet: str,
    language: str,
    tool_logs: Dict[str, Any],
    repo_path: str | None,
    target_file: str | None,
) -> Dict[str, Any]:
    file_context = ""
    file_context_label = ""
    if repo_path and target_file:
        file_context = f"Target file path (relative): {target_file}\nRepo path: {repo_path}\n"
        file_context_label = "- The repo path and target file path."

    return {
        "file_context": file_context,
        "file_context_label": file_context_label,
        "acceptance_criteria": acceptance_criteria,
        "original_snippet": original_snippet,
        "edited_snippet": edited_snippet,
        "language": language,
        "tool_logs_json": json.dumps(tool_logs, indent=2),
    }

