from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class LinePatch:
    file_path: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    replacement_lines: List[str]


def apply_line_patch(original_text: str, patch: LinePatch) -> str:
    """
    Apply a simple line-based patch to the given text.

    The patch replaces lines in the inclusive range [start_line, end_line]
    with `replacement_lines`.
    """
    lines = original_text.splitlines()

    if patch.start_line < 1 or patch.end_line < patch.start_line or patch.end_line > len(lines):
        raise ValueError("Invalid line range in patch")

    start_idx = patch.start_line - 1
    end_idx = patch.end_line  # slice end is exclusive

    new_lines = lines[:start_idx] + patch.replacement_lines + lines[end_idx:]
    return "\n".join(new_lines) + ("\n" if original_text.endswith("\n") else "")


def apply_line_patch_to_file(file_path: str | Path, patch: LinePatch) -> None:
    path = Path(file_path)
    original_text = path.read_text(encoding="utf8")
    new_text = apply_line_patch(original_text, patch)
    path.write_text(new_text, encoding="utf8")

