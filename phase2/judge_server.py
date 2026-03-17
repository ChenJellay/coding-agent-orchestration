from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import mlx_lm


# Model configuration
MODEL_PATH = os.environ.get("QWEN_MODEL_PATH", "mlx-community/Qwen3.5-9B-MLX-4bit")

_CACHED_MODEL: Any | None = None
_CACHED_TOKENIZER: Any | None = None
_CACHED_MODEL_ID: str | None = None


def _get_mlx_model() -> tuple[Any, Any]:
    global _CACHED_MODEL, _CACHED_TOKENIZER, _CACHED_MODEL_ID
    if (
        _CACHED_MODEL is not None
        and _CACHED_TOKENIZER is not None
        and _CACHED_MODEL_ID == MODEL_PATH
    ):
        return _CACHED_MODEL, _CACHED_TOKENIZER

    model, tokenizer = mlx_lm.load(MODEL_PATH)
    _CACHED_MODEL = model
    _CACHED_TOKENIZER = tokenizer
    _CACHED_MODEL_ID = MODEL_PATH
    return model, tokenizer


class JudgeRequestBody(BaseModel):
    # Optional file context for judge services that prefer reading from disk.
    repo_path: Optional[str] = None
    target_file: Optional[str] = None

    acceptance_criteria: str
    original_snippet: str
    edited_snippet: str
    language: str
    tool_logs: Dict[str, Any]


class JudgeResponseBody(BaseModel):
    verdict: str  # "PASS" or "FAIL"
    justification: str
    problematic_lines: List[int]


app = FastAPI(title="Local Judge Service", version="0.1.0")


class IntentCompilerRequestBody(BaseModel):
    macro_intent: str
    repo_path: str


class IntentNodeSpecBody(BaseModel):
    node_id: str
    description: str
    target_file: str
    acceptance_criteria: str


class IntentCompilerResponseBody(BaseModel):
    dag_id: str | None = None
    nodes: List[IntentNodeSpecBody]
    edges: List[List[str]]


def _build_intent_compiler_prompt(body: IntentCompilerRequestBody) -> str:
    return f"""
You are an intent compiler for a code-editing system.

You are given:
- A macro intent: a natural-language description of a desired change.
- A repo path: an absolute path to the repository root.

Your task:
- Break the macro intent into a small DAG of micro-tasks.
- Each node MUST target exactly one file (relative path from repo root).
- Each node MUST include acceptance criteria that a Judge can evaluate.
- Edges are pairs ["from", "to"] expressing dependencies.

Output format (must be the ONLY output):
{{
  "dag_id": "optional string",
  "nodes": [
    {{
      "node_id": "N1",
      "description": "short description",
      "target_file": "path/relative/to/repo",
      "acceptance_criteria": "clear, testable criteria"
    }}
  ],
  "edges": [
    ["N1", "N2"]
  ]
}}

Repo path: {body.repo_path}

Macro intent:
\"\"\"{body.macro_intent}\"\"\"
""".strip()


@app.post("/intent-compiler", response_model=IntentCompilerResponseBody)
def intent_compiler_endpoint(body: IntentCompilerRequestBody) -> IntentCompilerResponseBody:
    try:
        model, tokenizer = _get_mlx_model()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}") from exc

    prompt = _build_intent_compiler_prompt(body)

    make_sampler = getattr(mlx_lm, "make_sampler", None)
    if callable(make_sampler):
        sampler = make_sampler(temp=0.0)
        raw = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=1024,
            sampler=sampler,
        )
    else:
        raw = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=1024,
        )

    try:
        data = _parse_judge_json(raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Intent compiler returned invalid JSON: {exc}") from exc

    nodes_raw = data.get("nodes") or []
    edges_raw = data.get("edges") or []

    # Normalize nodes
    nodes: List[IntentNodeSpecBody] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        try:
            nodes.append(
                IntentNodeSpecBody(
                    node_id=str(n.get("node_id", "")),
                    description=str(n.get("description", "")),
                    target_file=str(n.get("target_file", "")),
                    acceptance_criteria=str(n.get("acceptance_criteria", "")),
                )
            )
        except Exception:
            continue

    # Normalize edges
    edges: List[List[str]] = []
    for e in edges_raw:
        if isinstance(e, (list, tuple)) and len(e) == 2:
            edges.append([str(e[0]), str(e[1])])

    return IntentCompilerResponseBody(
        dag_id=(str(data["dag_id"]) if "dag_id" in data and data["dag_id"] is not None else None),
        nodes=nodes,
        edges=edges,
    )


def _build_judge_prompt(body: JudgeRequestBody) -> str:
    file_context = ""
    if body.repo_path and body.target_file:
        file_context = f"Target file path (relative): {body.target_file}\nRepo path: {body.repo_path}\n"

    return f"""
You are a strict code change judge.

You are given:
- Acceptance criteria for a requested change.
- The original code snippet.
- The edited code snippet after an automated change.
- The programming language.
- Tool logs (e.g., static checks).
{('- The repo path and target file path.' if file_context else '')}

Your task:
- Decide if the edited snippet satisfies the acceptance criteria.
- If it does, return verdict "PASS".
- If it does not, return verdict "FAIL" and explain why.
- Optionally, list 1-based line numbers in the edited snippet that are problematic.

Output format (must be the ONLY output):
{{
  "verdict": "PASS" | "FAIL",
  "justification": "short explanation string",
  "problematic_lines": [1, 2, 3]
}}

Acceptance criteria:
\"\"\"{body.acceptance_criteria}\"\"\"

Language: {body.language}

{file_context}
Original snippet:
\"\"\"{body.original_snippet}\"\"\"

Edited snippet:
\"\"\"{body.edited_snippet}\"\"\"

Tool logs (JSON):
{json.dumps(body.tool_logs, indent=2)}
""".strip()


def _parse_judge_json(raw: str) -> Dict[str, Any]:
    # Log raw output for debugging
    print("\n===== Judge raw model output start =====")
    print(raw)
    print("===== Judge raw model output end =====\n")

    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

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
        raise ValueError("Unclosed JSON object in model output.")

    fragment = raw[start : end + 1]
    try:
        data = json.loads(fragment)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from model output: {exc}") from exc

    return data


@app.post("/judge", response_model=JudgeResponseBody)
def judge_endpoint(body: JudgeRequestBody) -> JudgeResponseBody:
    # If the caller provided file context, optionally refresh snippets from disk.
    # This supports judge implementations that want to inspect source-of-truth files.
    if body.repo_path and body.target_file:
        try:
            target_path = (Path(body.repo_path).resolve() / body.target_file).resolve()
            if target_path.exists():
                # Only replace edited_snippet if it looks empty; otherwise trust caller-provided content.
                if not body.edited_snippet.strip():
                    body.edited_snippet = target_path.read_text()
        except Exception as exc:
            return JudgeResponseBody(
                verdict="FAIL",
                justification=f"Judge failed to read target file from disk: {exc}",
                problematic_lines=[],
            )

    try:
        model, tokenizer = _get_mlx_model()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}") from exc

    prompt = _build_judge_prompt(body)

    # Use make_sampler if available to control temperature; otherwise rely on defaults.
    make_sampler = getattr(mlx_lm, "make_sampler", None)
    if callable(make_sampler):
        sampler = make_sampler(temp=0.0)
        raw = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=512,
            sampler=sampler,
        )
    else:
        raw = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=512,
        )

    try:
        data = _parse_judge_json(raw)
    except ValueError as exc:
        # Fall back to a FAIL verdict with the parsing error in justification.
        return JudgeResponseBody(
            verdict="FAIL",
            justification=f"Judge model returned invalid JSON: {exc}; raw={raw!r}",
            problematic_lines=[],
        )

    verdict = str(data.get("verdict", "FAIL")).upper()
    justification = str(data.get("justification", ""))
    problematic_lines_raw = data.get("problematic_lines") or []

    # Normalize problematic_lines to a list of ints.
    problematic_lines: List[int] = []
    for x in problematic_lines_raw:
        try:
            problematic_lines.append(int(x))
        except (TypeError, ValueError):
            continue

    if verdict not in {"PASS", "FAIL"}:
        verdict = "FAIL"

    return JudgeResponseBody(
        verdict=verdict,
        justification=justification,
        problematic_lines=problematic_lines,
    )

