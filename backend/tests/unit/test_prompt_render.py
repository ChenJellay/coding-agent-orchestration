"""Prompt templates must use str.format rules: literal braces doubled; placeholders {name} only."""

from __future__ import annotations

import pytest

from agenti_helix.agents.render import load_prompt_template, render_prompt


def test_coder_builder_prompt_renders_with_jsx_like_json_in_context() -> None:
    """Regression: file_contexts_json may contain `style={{ ... }}`; template must not use bare `{` in prose."""
    template = load_prompt_template("coder_builder.md")
    out = render_prompt(
        template,
        {
            "dag_task": "task",
            "acceptance_criteria": "ac",
            "file_contexts_json": '[{"content": "const x = <div style={{ margin: 1 }} />;"}]',
        },
    )
    assert "Current_DAG_Task" in out
    assert "style={{ margin: 1 }}" in out or "style=" in out


def test_render_prompt_raises_on_missing_key() -> None:
    template = "Hello {missing}"
    with pytest.raises(KeyError, match="Missing prompt template variable"):
        render_prompt(template, {})
