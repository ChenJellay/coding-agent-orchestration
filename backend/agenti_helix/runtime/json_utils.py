from __future__ import annotations

import json
from typing import Any, Dict


def extract_first_json_object(raw: str) -> Dict[str, Any]:
    """
    Extract and parse the first top-level JSON object from a model response.

    Many local models emit surrounding text; we recover by locating the first '{'
    and matching braces to the corresponding closing '}'.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("Model output did not contain a JSON object (no '{' found).")

    depth = 0
    end = None
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end is None:
        raise ValueError("Model output appears to start a JSON object but never closes it.")

    fragment = raw[start : end + 1]
    return json.loads(fragment)

