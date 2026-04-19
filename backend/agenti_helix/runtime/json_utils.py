from __future__ import annotations

import json
import re
import sys
import time
from json import JSONDecoder
from pathlib import Path
from typing import Any, Dict, List

try:
    import json5  # type: ignore[import-untyped]

    _HAS_JSON5 = True
except ImportError:
    json5 = None  # type: ignore[assignment]
    _HAS_JSON5 = False


# ── Thinking-block stripper ──────────────────────────────────────────────
# Qwen3 and similar reasoning models emit a <think>…</think> block before
# the actual answer.  We strip these so the JSON extractor sees only the
# final structured output.  Handles multiline and multiple blocks.
_THINK_RE = re.compile(r"<redacted_thinking>.*?</redacted_thinking>", re.DOTALL)


def strip_thinking_blocks(raw: str) -> str:
    """Remove all ``<think>…</think>`` sections from model output."""
    return _THINK_RE.sub("", raw).strip()


def strip_model_chat_suffixes(raw: str) -> str:
    """
    Truncate at chat-template tokens some models append after the JSON object
    (e.g. <|endoftext|>, <|im_start|>).
    """
    out = raw
    for marker in ("<|endoftext|>", "<|im_start|>", "<|redacted_im_end|>"):
        i = out.find(marker)
        if i != -1:
            out = out[:i]
    return out.rstrip()


def _strip_trailing_commas(fragment: str) -> str:
    """Remove trailing commas before ``}`` or ``]`` (invalid in strict JSON, common in LLM output)."""
    out = fragment
    prev = None
    while prev != out:
        prev = out
        out = re.sub(r",(\s*[}\]])", r"\1", out)
    return out


def _normalize_unicode_quotes(fragment: str) -> str:
    """Replace curly/smart quotes that break ``json.loads``."""
    return (
        fragment.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _parse_json_fragment_loose(fragment: str) -> Dict[str, Any]:
    """
    Parse a JSON object string with LLM-tolerant fallbacks (trailing commas, unicode quotes,
    Python 3.12+ relaxed string rules, optional json5).
    """
    fragment = fragment.strip()
    candidates: List[str] = [fragment]
    tc = _strip_trailing_commas(fragment)
    if tc != fragment:
        candidates.append(tc)
    nq = _normalize_unicode_quotes(fragment)
    if nq != fragment:
        candidates.append(nq)
        candidates.append(_strip_trailing_commas(nq))

    last_err: Exception | None = None
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError as e:
            last_err = e
            continue

    if sys.version_info >= (3, 12):
        for c in candidates:
            try:
                return json.loads(c, strict=False)
            except json.JSONDecodeError as e:
                last_err = e
                continue

    if _HAS_JSON5 and json5 is not None:
        for c in candidates:
            try:
                parsed = json5.loads(c)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError) as e:
                last_err = e
                continue

    if isinstance(last_err, json.JSONDecodeError):
        raise last_err
    if last_err is not None:
        raise last_err
    raise json.JSONDecodeError("Unable to parse JSON fragment", fragment, 0)


# Shown when judge JSON cannot be parsed and justification cannot be recovered — must not imply the coder broke JSON.
JUDGE_JSON_FALLBACK_CANON_MSG = (
    "Judge model output failed strict JSON parse (often unescaped double-quotes in the judge's justification field). "
    "The verdict was still recovered by the runtime parser; inspect judge raw_output if needed. "
    "This is not a failure of the coder's patch JSON."
)


def _extract_judge_justification_loose(cleaned: str) -> str:
    """
    When ``justification`` contains raw ``"`` characters, strict JSON parsers fail.  We take the substring
    between the opening ``"`` after ``justification`` and the ``problematic_lines`` key — the same span a
    human reader would use — then decode escapes when possible.
    """
    pl_key = '"problematic_lines"'
    pl_idx = cleaned.find(pl_key)
    if pl_idx == -1:
        return ""
    jm = re.search(r'"justification"\s*:\s*"', cleaned, re.DOTALL)
    if not jm:
        return ""
    val_start = jm.end()
    if val_start >= pl_idx:
        return ""
    segment = cleaned[val_start:pl_idx].rstrip()
    segment = re.sub(r'",\s*$', "", segment)
    if not segment:
        return ""
    try:
        return json.loads('"' + segment + '"')
    except json.JSONDecodeError:
        return segment


def try_fallback_snippet_judge_dict(raw: str) -> Dict[str, Any] | None:
    """
    Recover ``SnippetJudgeOutput`` fields when strict JSON parsing fails — commonly
    because ``justification`` contains unescaped ``"`` (e.g. code like ``"purple"``),
    which breaks ``json.loads`` / json5 and yields errors like ``Unexpected \"\"\"``.
    """
    cleaned = strip_thinking_blocks(raw)
    cleaned = strip_model_chat_suffixes(cleaned)
    verdict_m = re.search(r'"verdict"\s*:\s*"(PASS|FAIL)"', cleaned, re.I)
    if not verdict_m:
        return None
    verdict = verdict_m.group(1).upper()

    pl_m = re.search(r'"problematic_lines"\s*:\s*(\[[^\]]*\])', cleaned)
    problematic_lines: List[int] = []
    if pl_m:
        try:
            pl_val = json.loads(pl_m.group(1))
            if isinstance(pl_val, list):
                problematic_lines = [int(x) for x in pl_val if isinstance(x, (int, float))]
        except (json.JSONDecodeError, TypeError, ValueError):
            problematic_lines = []

    justification = _extract_judge_justification_loose(cleaned)
    recover_method = "loose_slice" if justification else ""

    jm = re.search(
        r'"justification"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"problematic_lines"',
        cleaned,
        re.DOTALL,
    )
    if jm:
        try:
            inner = jm.group(1)
            justification = json.loads('"' + inner + '"')
            recover_method = "strict_regex"
        except (json.JSONDecodeError, ValueError):
            if not justification:
                recover_method = ""

    if not justification:
        justification = JUDGE_JSON_FALLBACK_CANON_MSG
        recover_method = "canned"

    # #region agent log
    try:
        _p = Path("/Users/jerrychen/startup/coding-agent-orchestration/.cursor/debug-9274ce.log")
        _line = json.dumps(
            {
                "sessionId": "9274ce",
                "timestamp": int(time.time() * 1000),
                "location": "json_utils.py:try_fallback_snippet_judge_dict",
                "message": "judge JSON fallback used",
                "hypothesisId": "H1",
                "data": {
                    "verdict": verdict,
                    "recover_method": recover_method,
                    "justification_len": len(justification),
                },
                "runId": "judge_fallback",
            }
        ) + "\n"
        _p.parent.mkdir(parents=True, exist_ok=True)
        with _p.open("a", encoding="utf-8") as _f:
            _f.write(_line)
    except Exception:
        pass
    # #endregion

    return {
        "verdict": verdict,
        "justification": justification,
        "problematic_lines": problematic_lines,
    }


def extract_first_json_object(raw: str) -> Dict[str, Any]:
    """
    Extract and parse the first top-level JSON object from a model response.

    Many local models emit surrounding text; we recover by locating the first '{'
    and matching braces to the corresponding closing '}'.

    **Thinking support**: if the model output contains ``<think>…</think>``
    blocks (Qwen3, DeepSeek-R1, etc.), they are stripped before extraction
    so that reasoning text doesn't interfere with brace matching.

    This implementation is string-aware: braces inside JSON string literals
    (delimited by `"`) are ignored.  Backslash-escaped quotes (`\\"`) inside
    strings are handled correctly so that code containing braces in
    replacementLines (JSX `style={{}}`, function bodies, etc.) does not
    confuse the brace counter.
    """
    # Strip thinking blocks first so reasoning prose doesn't confuse
    # brace counting (thinking blocks often contain code snippets with braces).
    cleaned = strip_thinking_blocks(raw)
    cleaned = strip_model_chat_suffixes(cleaned)

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("Model output did not contain a JSON object (no '{' found).")

    # Prefer the stdlib decoder: parses the first complete JSON value from `start`
    # and ignores trailing prose (models often append extra text after the object).
    try:
        obj, _end = JSONDecoder().raw_decode(cleaned, start)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # Try tolerant parse on the full tail (trailing commas / json5) before brace matching.
    tail = cleaned[start:].strip()
    try:
        return _parse_json_fragment_loose(tail)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    depth = 0
    in_string = False
    end = None
    i = start
    while i < len(cleaned):
        ch = cleaned[i]

        if in_string:
            if ch == "\\" and i + 1 < len(cleaned):
                # Skip the escaped character (e.g. \", \\, \n).
                i += 2
                continue
            if ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        i += 1

    if end is None:
        raise ValueError("Model output appears to start a JSON object but never closes it.")

    fragment = cleaned[start : end + 1]
    return _parse_json_fragment_loose(fragment)


def extract_json_dict_prefer_markdown_fences(raw: str) -> Dict[str, Any]:
    """
    Prefer a JSON object inside ```json ... ``` / ``` ... ``` fences (last fence wins).

    Some agents (e.g. ``supreme_court_v1``) emit long prose and then a fenced JSON block.
    ``extract_first_json_object`` can mis-parse when the first ``{`` in the text is not the
    start of the final answer object. Fences delimit the intended payload reliably.
    """
    cleaned = strip_thinking_blocks(raw)
    cleaned = strip_model_chat_suffixes(cleaned)

    payloads: List[str] = []
    i = 0
    while i < len(cleaned):
        m = re.search(r"```(?:json)?\s*", cleaned[i:], re.IGNORECASE)
        if not m:
            break
        abs_start = i + m.end()
        close = cleaned.find("```", abs_start)
        if close == -1:
            break
        payloads.append(cleaned[abs_start:close].strip())
        i = close + 3

    for p in reversed(payloads):
        if "{" not in p:
            continue
        start = p.find("{")
        tail = p[start:].strip()
        try:
            obj, _end = JSONDecoder().raw_decode(tail, 0)
            if isinstance(obj, dict) and obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            loose = _parse_json_fragment_loose(tail)
            if isinstance(loose, dict) and loose:
                return loose
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    return extract_first_json_object(cleaned)

