"""SDET context clipping + schema caps (truncated JSON prevention)."""

from __future__ import annotations

import json

import pytest

from agenti_helix.agents import models
from agenti_helix.runtime.tools import tool_truncate_file_contexts_for_sdet


def test_truncate_file_contexts_for_sdet_caps_long_files() -> None:
    huge = "x" * 50_000
    payload = [{"file_path": "src/a.js", "content": huge, "exists": True, "required_symbols": []}]
    out = tool_truncate_file_contexts_for_sdet(
        file_contexts_json=json.dumps(payload),
        max_chars_per_file=500,
    )
    rows = json.loads(out["file_contexts_json"])
    assert len(rows[0]["content"]) < len(huge)
    assert "truncated for SDET" in rows[0]["content"]
    assert out["truncated_files"] == ["src/a.js"]


def test_sdet_output_rejects_oversized_test_body() -> None:
    body = "l\n" * 12_000
    with pytest.raises(ValueError, match="under 10000"):
        models.SdetOutput(testing_strategy="short", test_files=[models.CodeFile(file_path="t.test.js", content=body)])


def test_sdet_output_rejects_more_than_two_files() -> None:
    files = [
        models.CodeFile(file_path=f"t{i}.test.js", content="it('x',()=>{});\n")
        for i in range(3)
    ]
    with pytest.raises(Exception):  # pydantic list length
        models.SdetOutput(testing_strategy="s", test_files=files)
