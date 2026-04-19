"""Tests for JSON extraction when models wrap answers in markdown fences."""

from __future__ import annotations

from agenti_helix.runtime.json_utils import (
    extract_first_json_object,
    extract_json_dict_prefer_markdown_fences,
    strip_thinking_blocks,
)


def test_fence_wins_over_leading_prose() -> None:
    raw = """Some analysis text without braces first.

```json
{"resolved": true, "reasoning": "ok", "filePath": "a.js", "startLine": 1, "endLine": 1, "replacementLines": ["x"], "compromise_summary": "y"}
```
"""
    d = extract_json_dict_prefer_markdown_fences(raw)
    assert d["resolved"] is True
    assert d["filePath"] == "a.js"


def test_fallback_to_first_object_when_no_fence() -> None:
    raw = '{"resolved": false, "reasoning": "nope"}'
    d = extract_json_dict_prefer_markdown_fences(raw)
    assert d["resolved"] is False


def test_strip_redacted_thinking_then_parse_json() -> None:
    """Prompts use <redacted_thinking>…</redacted_thinking>; JSON must not be parsed from inside it."""
    raw = """<redacted_thinking>
Prose with a stray { brace.
</redacted_thinking>
{"compressed_summary": "ok", "key_constraints": ["a"]}
"""
    stripped = strip_thinking_blocks(raw)
    assert "stray" not in stripped
    d = extract_first_json_object(raw)
    assert d["compressed_summary"] == "ok"
