from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.request

from agenti_helix.observability.debug_log import log_event

from .config import DEFAULT_CONFIG


@dataclass
class JudgeRequest:
    """Payload sent to the local Judge model service."""

    # Optional file context for judge services that prefer reading from disk.
    # (Snippet-only judges can ignore these fields.)
    repo_path: Optional[str]
    target_file: Optional[str]

    acceptance_criteria: str
    original_snippet: str
    edited_snippet: str
    language: str
    tool_logs: Dict[str, Any]


@dataclass
class JudgeResponse:
    """Result returned from the local Judge model service."""

    verdict: str  # expected values: "PASS" or "FAIL"
    justification: str
    problematic_lines: List[int]

    @property
    def is_pass(self) -> bool:
        return self.verdict.upper() == "PASS"


class JudgeClient:
    """Thin HTTP client for the local Judge service."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        cfg = DEFAULT_CONFIG
        self._base_url = (base_url or cfg.judge_base_url).rstrip("/")
        self._timeout = timeout_seconds if timeout_seconds is not None else cfg.judge_timeout_seconds

    def evaluate(self, request: JudgeRequest) -> JudgeResponse:
        """
        Send a JudgeRequest to the local service and return its JudgeResponse.

        On transport errors, returns a FAIL verdict with justification.
        """
        url = f"{self._base_url}/judge"
        payload = json.dumps(asdict(request)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = (os.environ.get("AGENTI_HELIX_JUDGE_SERVICE_TOKEN") or "").strip()
        if token:
            headers["X-Agenti-Helix-Judge-Token"] = token
        http_request = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            log_event(
                run_id="pre",
                hypothesis_id="H4",
                location="agenti_helix/verification/judge_client.py:JudgeClient.evaluate",
                message="Judge transport error",
                data={"base_url": self._base_url, "error": str(exc)},
            )
            return JudgeResponse(
                verdict="FAIL",
                justification=f"Transport error talking to Judge service: {exc}",
                problematic_lines=[],
            )

        try:
            data: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_event(
                run_id="pre",
                hypothesis_id="H4",
                location="agenti_helix/verification/judge_client.py:JudgeClient.evaluate",
                message="Judge returned invalid JSON",
                data={"base_url": self._base_url, "error": str(exc), "raw": raw[:500]},
            )
            return JudgeResponse(
                verdict="FAIL",
                justification=f"Invalid JSON from Judge service: {exc}; payload={raw!r}",
                problematic_lines=[],
            )

        verdict = str(data.get("verdict", "FAIL")).upper()
        justification = str(data.get("justification", ""))
        problematic_lines_raw = data.get("problematic_lines") or []
        problematic_lines = [int(x) for x in problematic_lines_raw]

        log_event(
            run_id="pre",
            hypothesis_id="H4",
            location="agenti_helix/verification/judge_client.py:JudgeClient.evaluate",
            message="Judge responded",
            data={"base_url": self._base_url, "verdict": verdict, "problematic_lines": problematic_lines},
        )
        return JudgeResponse(
            verdict=verdict,
            justification=justification,
            problematic_lines=problematic_lines,
        )

