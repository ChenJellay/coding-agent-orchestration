from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agenti_helix.runtime.agent_runtime import run_agent
from agenti_helix.agents.registry import get_agent
from agenti_helix.observability.debug_log import log_event


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


_rate_lock = Lock()
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _client_host(request: Request) -> str:
    c = request.client
    return c[0] if c else "unknown"


def _rate_limited(host: str, limit: int) -> bool:
    if limit <= 0:
        return False
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[host]
        bucket[:] = [t for t in bucket if now - t < 60.0]
        if len(bucket) >= limit:
            return True
        bucket.append(now)
    return False


class JudgeSecurityMiddleware(BaseHTTPMiddleware):
    """Optional shared token + per-IP POST rate limit (see env vars)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path in ("/docs", "/openapi.json", "/redoc", "/"):
            return await call_next(request)
        expected = (os.environ.get("AGENTI_HELIX_JUDGE_SERVICE_TOKEN") or "").strip()
        if expected:
            if request.headers.get("X-Agenti-Helix-Judge-Token") != expected:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            limit = int(os.environ.get("AGENTI_HELIX_JUDGE_RATE_LIMIT_PER_MIN", "240") or "240")
        except ValueError:
            limit = 240
        if request.method == "POST" and _rate_limited(_client_host(request), limit):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        return await call_next(request)


app = FastAPI(title="Local Judge Service", version="0.1.0")

# D2: Restrict CORS to the control-plane API only; never expose to browsers directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8001", "http://localhost:8001"],
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Agenti-Helix-Judge-Token"],
)

# Outermost: token + rate limit before CORS-wrapped routes.
app.add_middleware(JudgeSecurityMiddleware)


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
    agent = get_agent("intent_compiler_v1")
    return agent.render({"macro_intent": body.macro_intent, "repo_path": body.repo_path})


@app.post("/intent-compiler", response_model=IntentCompilerResponseBody)
def intent_compiler_endpoint(body: IntentCompilerRequestBody) -> IntentCompilerResponseBody:
    try:
        typed = run_agent(
            agent_id="intent_compiler_v1",
            raw_input={"macro_intent": body.macro_intent, "repo_path": body.repo_path},
            runtime={"temperature": 0.0},
            observe={
                "run_id": "judge_service",
                "hypothesis_id": "intent_compiler",
                "location": "judge_server:POST /intent-compiler",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Intent compiler failed: {exc}") from exc

    nodes_raw = typed.get("nodes") or []
    edges_raw = typed.get("edges") or []

    nodes: List[IntentNodeSpecBody] = [
        IntentNodeSpecBody(
            node_id=str(n.get("node_id") or ""),
            description=str(n.get("description") or ""),
            target_file=str(n.get("target_file") or ""),
            acceptance_criteria=str(n.get("acceptance_criteria") or ""),
        )
        for n in nodes_raw
        if isinstance(n, dict)
    ]

    edges: List[List[str]] = []
    for e in edges_raw:
        if isinstance(e, (list, tuple)) and len(e) == 2:
            edges.append([str(e[0]), str(e[1])])

    dag_id = typed.get("dag_id")
    return IntentCompilerResponseBody(dag_id=str(dag_id) if dag_id is not None else None, nodes=nodes, edges=edges)


def _build_judge_prompt(body: JudgeRequestBody) -> str:
    agent = get_agent("judge_v1")
    return agent.render(
        {
            "repo_path": body.repo_path,
            "target_file": body.target_file,
            "acceptance_criteria": body.acceptance_criteria,
            "original_snippet": body.original_snippet,
            "edited_snippet": body.edited_snippet,
            "language": body.language,
            "tool_logs_json": json.dumps(body.tool_logs, indent=2),
        }
    )


def _parse_model_json(raw: str) -> Dict[str, Any]:
    # D2: Replace raw print with structured log (controlled by AGENTI_HELIX_DISABLE_LOGGING).
    log_event(
        run_id="judge_server",
        hypothesis_id="model_output",
        location="agenti_helix/verification/judge_server.py:_parse_model_json",
        message="Raw model output received",
        data={"length": len(raw), "preview": raw[:200]},
    )

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
        return json.loads(fragment)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from model output: {exc}") from exc


@app.post("/judge", response_model=JudgeResponseBody)
def judge_endpoint(body: JudgeRequestBody) -> JudgeResponseBody:
    if body.repo_path and body.target_file:
        try:
            target_path = (Path(body.repo_path).resolve() / body.target_file).resolve()
            if target_path.exists():
                if not body.edited_snippet.strip():
                    body.edited_snippet = target_path.read_text()
        except Exception as exc:
            return JudgeResponseBody(
                verdict="FAIL",
                justification=f"Judge failed to read target file from disk: {exc}",
                problematic_lines=[],
            )

    try:
        typed = run_agent(
            agent_id="judge_v1",
            raw_input={
                "repo_path": body.repo_path,
                "target_file": body.target_file,
                "acceptance_criteria": body.acceptance_criteria,
                "original_snippet": body.original_snippet,
                "edited_snippet": body.edited_snippet,
                "language": body.language,
                "tool_logs_json": json.dumps(body.tool_logs, indent=2),
            },
            runtime={"temperature": 0.0},
            observe={
                "run_id": "judge_service",
                "hypothesis_id": "judge_v1",
                "location": "judge_server:POST /judge",
            },
        )
    except Exception as exc:
        return JudgeResponseBody(
            verdict="FAIL",
            justification=f"Judge model failed: {exc}",
            problematic_lines=[],
        )

    verdict = str(typed.get("verdict", "FAIL")).upper()
    if verdict not in {"PASS", "FAIL"}:
        verdict = "FAIL"
    justification = str(typed.get("justification", "") or "")
    problematic_lines_raw = typed.get("problematic_lines") or []
    problematic_lines = [int(x) for x in problematic_lines_raw if str(x).isdigit() or isinstance(x, int)]
    return JudgeResponseBody(
        verdict=verdict,
        justification=justification,
        problematic_lines=problematic_lines,
    )

