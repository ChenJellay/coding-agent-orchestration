"""Regression tests for full-pipeline tooling (diff snapshots, Jest fallback)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenti_helix.agents.render import load_prompt_template, render_prompt
from agenti_helix.orchestration.intent_compiler import (
    _coder_task_intent_for_node,
    enrich_macro_intent_with_doc_before_compile,
)

from agenti_helix.runtime.tools import (
    _discover_jest_config,
    _js_tests_likely_need_jsdom,
    tool_write_all_files,
)


def test_enrich_macro_intent_no_doc_returns_unchanged(tmp_path: Path) -> None:
    """Without a doc URL or uploaded text, enrichment is a no-op."""
    out, effective, merged = enrich_macro_intent_with_doc_before_compile(
        "plan the feature",
        repo_path=str(tmp_path),
        dag_id="dag-x",
        doc_url=None,
        doc_text=None,
        doc_filename=None,
    )
    assert out == "plan the feature"
    assert effective == ""
    assert merged is False


def test_coder_task_intent_prioritizes_node_goal_over_macro() -> None:
    s = _coder_task_intent_for_node(
        node_id="N2",
        description="Change the label on the save button.",
        acceptance_criteria="Button reads Save.",
        macro_intent="Rebuild the entire dashboard and add analytics.",
    )
    assert "N2" in s
    assert "save button" in s.lower()
    assert "Rebuild the entire dashboard" in s
    assert s.index("### Goal") < s.index("Product context")


def test_doc_fetcher_prompt_renders_json_examples_without_format_keyerror() -> None:
    """JSON examples in doc_fetcher.md must use {{ }} so str.format does not eat \"doc_url\" keys."""
    template = load_prompt_template("doc_fetcher.md")
    out = render_prompt(
        template,
        {
            "doc_url": "file:///repo/.agenti_helix/x.md",
            "doc_content": "body",
            "intent": "i",
            "target_file": "t.js",
            "acceptance_criteria": "a",
        },
    )
    assert "file:///repo/.agenti_helix/x.md" in out
    assert "body" in out


def test_diff_validator_prompt_renders_json_examples_without_format_keyerror() -> None:
    """JSON examples must use {{ }} so str.format does not treat \"verdict\" as a placeholder."""
    template = load_prompt_template("diff_validator.md")
    out = render_prompt(
        template,
        {
            "intent": "i",
            "target_file": "t.js",
            "acceptance_criteria": "a",
            "git_diff": "diff --git",
            "allowed_paths": '["t.js"]',
            "repo_rules_text": "{}",
        },
    )
    assert '"verdict": "WARN"' in out


def test_type_checker_prompt_renders_json_examples_without_format_keyerror() -> None:
    template = load_prompt_template("type_checker.md")
    out = render_prompt(
        template,
        {
            "target_file": "t.ts",
            "language": "typescript",
            "file_content": "x",
            "type_checker_output": "error TS2345",
            "intent": "i",
            "acceptance_criteria": "a",
        },
    )
    assert '"type_health"' in out


def test_linter_prompt_renders_json_examples_without_format_keyerror() -> None:
    template = load_prompt_template("linter.md")
    out = render_prompt(
        template,
        {
            "target_file": "t.ts",
            "language": "typescript",
            "file_content": "x",
            "linter_raw_output": "err",
            "acceptance_criteria": "a",
        },
    )
    assert '"finding_count"' in out


def test_write_all_files_includes_snapshots_in_diff_json_str(tmp_path: Path) -> None:
    out = tool_write_all_files(
        repo_root=tmp_path,
        modified_files=[{"file_path": "src/a.js", "content": "console.log(1);\n"}],
        test_files=[{"file_path": "src/a.test.js", "content": "it('x', () => {});\n"}],
    )
    assert "file_snapshots" in out
    assert len(out["file_snapshots"]) == 2
    assert out["diff_validator_allowed_paths"] == ["src/a.js", "src/a.test.js"]
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


def test_write_all_files_blocks_mass_delete_in_existing_test(tmp_path: Path) -> None:
    p = tmp_path / "src" / "index.test.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"// line {i}\n" for i in range(1, 22))
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing write"):
        tool_write_all_files(
            repo_root=tmp_path,
            modified_files=[{"file_path": "src/index.test.js", "content": "it('x', () => {});\n"}],
        )


def test_write_all_files_blocks_jest_to_vitest_import_swap(tmp_path: Path) -> None:
    p = tmp_path / "src" / "index.test.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "import { describe, it, expect } from '@jest/globals';\n"
        "describe('app', () => { it('loads', () => { expect(1).toBe(1); }); });\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Vitest"):
        tool_write_all_files(
            repo_root=tmp_path,
            modified_files=[
                {
                    "file_path": "src/index.test.js",
                    "content": "import { describe, it, expect } from 'vitest';\n"
                    "describe('app', () => { it('loads', () => { expect(1).toBe(1); }); });\n",
                }
            ],
        )


def test_write_all_files_allows_guard_bypass_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTI_HELIX_DISABLE_TEST_REWRITE_GUARD", "1")
    p = tmp_path / "src" / "index.test.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"// line {i}\n" for i in range(1, 22))
    p.write_text(body, encoding="utf-8")
    out = tool_write_all_files(
        repo_root=tmp_path,
        modified_files=[{"file_path": "src/index.test.js", "content": "it('x', () => {});\n"}],
    )
    assert out["files_written"] == ["src/index.test.js"]


def test_diff_json_for_judge_gate_backfills_allowed_paths() -> None:
    from agenti_helix.verification.verification_loop import _diff_json_for_judge_gate

    out = _diff_json_for_judge_gate({"files_written": ["src/a.js"], "test_file_paths": ["src/a.test.js"]})
    assert out["diff_validator_allowed_paths"] == ["src/a.js", "src/a.test.js"]
