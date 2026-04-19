"""Regression tests for full-pipeline tooling (diff snapshots, Jest fallback)."""

from __future__ import annotations

import json
from pathlib import Path

from agenti_helix.agents.render import load_prompt_template, render_prompt
from agenti_helix.runtime.tools import (
    _discover_jest_config,
    _js_tests_likely_need_jsdom,
    tool_write_all_files,
)


def test_memory_summarizer_prompt_renders_without_format_keyerror() -> None:
    template = load_prompt_template("memory_summarizer.md")
    rendered = render_prompt(
        template,
        {
            "errors": "judge failed",
            "previous_patches": "{}",
            "attempt": 1,
        },
    )
    assert "compressed_summary" in rendered
    assert "{errors}" not in rendered


def test_write_all_files_includes_snapshots_in_diff_json_str(tmp_path: Path) -> None:
    out = tool_write_all_files(
        repo_root=tmp_path,
        modified_files=[{"file_path": "src/a.js", "content": "console.log(1);\n"}],
        test_files=[{"file_path": "src/a.test.js", "content": "it('x', () => {});\n"}],
    )
    assert "file_snapshots" in out
    assert len(out["file_snapshots"]) == 2
    parsed = json.loads(out["diff_json_str"])
    assert "file_snapshots" in parsed
    paths = {s["path"] for s in parsed["file_snapshots"]}
    assert paths == {"src/a.js", "src/a.test.js"}
    snap = next(s for s in parsed["file_snapshots"] if s["path"] == "src/a.js")
    assert "console.log" in snap["content"]


def test_write_all_files_dedupes_same_path_in_code_and_tests(tmp_path: Path) -> None:
    """LLMs sometimes emit the same path under modified_files and test_files."""
    body = "export function App() { return null; }\n"
    out = tool_write_all_files(
        repo_root=tmp_path,
        modified_files=[{"file_path": "src/index.jsx", "content": body}],
        test_files=[{"file_path": "src/index.jsx", "content": body}],
    )
    assert len(out["file_snapshots"]) == 1
    assert out["file_snapshots"][0]["path"] == "src/index.jsx"


def test_discover_jest_config_finds_standard_name(tmp_path: Path) -> None:
    (tmp_path / "jest.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
    assert _discover_jest_config(tmp_path) == tmp_path / "jest.config.cjs"


def test_jsdom_heuristic_detects_react_tests(tmp_path: Path) -> None:
    t = tmp_path / "t.test.js"
    t.write_text('import React from "react";\n', encoding="utf-8")
    assert _js_tests_likely_need_jsdom(tmp_path, ["t.test.js"]) is True


def test_jsdom_heuristic_plain_node(tmp_path: Path) -> None:
    t = tmp_path / "plain.test.js"
    t.write_text("const assert = require('assert');\n", encoding="utf-8")
    assert _js_tests_likely_need_jsdom(tmp_path, ["plain.test.js"]) is False
