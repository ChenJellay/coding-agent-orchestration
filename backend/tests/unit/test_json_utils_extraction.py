"""Tests for LLM-tolerant JSON extraction (thinking tags, markdown fences)."""

from __future__ import annotations

import pytest

from agenti_helix.runtime.json_utils import extract_first_json_object, strip_markdown_json_fences


def test_extract_first_json_object_typo_thinking_close_tag() -> None:
    raw = """<redacted_thinking>reason here { not json }</redacted_thicked_thinking>
{"search_strategy": "ok", "candidate_paths": ["a.js"]}
"""
    obj = extract_first_json_object(raw)
    assert obj["search_strategy"] == "ok"
    assert obj["candidate_paths"] == ["a.js"]


def test_extract_first_json_object_markdown_json_fence() -> None:
    raw = """<redacted_thinking>x</redacted_thinking>
```json
{"foo": 1, "bar": "baz"}
```
"""
    obj = extract_first_json_object(raw)
    assert obj == {"foo": 1, "bar": "baz"}


def test_strip_markdown_json_fence_no_newline_after_fence() -> None:
    raw = '```json{"x": 1}\n```'
    inner = strip_markdown_json_fences(raw)
    assert inner == '{"x": 1}'


def test_extract_first_json_object_fence_no_newline_after_fence() -> None:
    raw = """<redacted_thinking>t</redacted_thinking>
```json{"k": true}
```
"""
    obj = extract_first_json_object(raw)
    assert obj == {"k": True}


def test_extract_first_json_object_invalid_raises() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        extract_first_json_object("no braces here")


def test_extract_first_json_object_unclosed_thinking_then_json() -> None:
    raw = """<redacted_thinking>
Long reasoning without a closing tag — model stopped mid-stream.
Brace examples: { foo: 1 } in prose.
{"implementation_logic": "x", "modified_files": [], "missing_context": null}
"""
    obj = extract_first_json_object(raw)
    assert obj["implementation_logic"] == "x"
    assert obj["modified_files"] == []
    assert obj["missing_context"] is None


def test_extract_first_json_object_second_brace_is_real_object() -> None:
    raw = """<think>t</redacted_thinking>
Not JSON: { broken
{"implementation_logic": "ok", "modified_files": [], "missing_context": null}
"""
    obj = extract_first_json_object(raw)
    assert obj["implementation_logic"] == "ok"
