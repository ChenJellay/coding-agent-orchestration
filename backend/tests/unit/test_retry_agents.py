"""Integration tests for memory_summarizer_v1 + supreme_court_v1 wiring in the
verification loop.

These agents are opt-in on ``EditTaskSpec`` and interact with the loop at two
surgical points:

- ``_prepare_retry`` replaces the raw judge justification in ``state.feedback``
  with a focused hint from ``memory_summarizer_v1``.
- ``_finalise_after_retries`` consults ``supreme_court_v1`` when retries
  exhaust, and dispatches on its ``ruling`` field (PASS_OVERRIDE /
  ESCALATE_HUMAN / CONFIRM_BLOCKED).

The tests stub ``run_chain`` (coder + judge) and ``run_agent_structured``
(the two retry agents) so the loop can be driven deterministically without a
model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from agenti_helix.api.paths import HelixPaths
from agenti_helix.verification import checkpointing as cp_mod
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification import verification_loop as vloop


# --- shared fixtures (mirror test_phase2_verification_loop patterns) --------


def _isolate_helix_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cp_mod, "PATHS", HelixPaths(repo_root=tmp_path))


def _make_demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    header = src / "header.js"
    header.write_text('<button style={{ backgroundColor: "blue" }}>Click me</button>\n')
    return repo


def _stub_chain_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        "agenti_helix.orchestration.master_orchestrator.resolve_coder_chain",
        lambda task: {"steps": []},
    )
    monkeypatch.setattr(
        "agenti_helix.orchestration.master_orchestrator.resolve_judge_chain",
        lambda task: {"steps": []},
    )


def _stub_static_checks(monkeypatch, *, security_blocked: bool = False, errors: List[str] | None = None) -> None:
    monkeypatch.setattr(
        vloop,
        "_run_static_checks",
        lambda repo_root, target_file: {
            "passed": not (errors or security_blocked),
            "errors": errors or [],
            "checks_run": ["stub"],
            "security_blocked": security_blocked,
        },
    )


def _fake_run_chain_fail(*, target: Path) -> Callable[..., Dict[str, Any]]:
    """Coder writes a broken body and judge always FAILs."""

    def run_chain(*, chain_spec, initial_context, location_prefix, **_kwargs):
        ctx = dict(initial_context)
        if "_run_coder" in location_prefix:
            target.write_text("BROKEN\n")
            ctx["diff_json"] = {
                "filePath": "src/components/header.js",
                "startLine": 1,
                "endLine": 1,
            }
        elif "_call_judge" in location_prefix:
            ctx["judge_response"] = {
                "verdict": "FAIL",
                "justification": "stubbed FAIL",
                "problematic_lines": [],
            }
        return ctx

    return run_chain


# --- memory_summarizer_v1 --------------------------------------------------


def test_memory_summarizer_rewrites_feedback_between_retries(tmp_path: Path, monkeypatch) -> None:
    repo = _make_demo_repo(tmp_path)
    target = repo / "src" / "components" / "header.js"
    monkeypatch.chdir(tmp_path)
    _isolate_helix_paths(monkeypatch, tmp_path)
    _stub_chain_resolution(monkeypatch)
    _stub_static_checks(monkeypatch)
    monkeypatch.setattr(vloop, "run_chain", _fake_run_chain_fail(target=target))

    calls: List[Dict[str, Any]] = []

    def fake_structured(*, agent_id: str, raw_input: Dict[str, Any], **_kwargs) -> Dict[str, Any]:
        calls.append({"agent_id": agent_id, "raw_input": raw_input})
        assert agent_id == "memory_summarizer_v1"
        return {
            "root_cause_hypothesis": "coder keeps replacing the whole file",
            "actionable_hint": "edit only the button style block, not the entire component",
            "anti_patterns_to_avoid": ["rewriting the full file", "touching header.test.js"],
        }

    # Patch the symbol at the import site used by verification_loop's lazy import.
    monkeypatch.setattr(
        "agenti_helix.runtime.structured_output.run_agent_structured",
        fake_structured,
    )

    task = EditTaskSpec(
        task_id="t-mem",
        intent="make button green",
        target_file="src/components/header.js",
        acceptance_criteria="button is green",
        repo_path=str(repo),
        enable_memory_summarizer=True,
    )

    state = vloop.run_verification_loop(task)

    # memory_summarizer_v1 ran at least once (between first FAIL and retry).
    assert any(c["agent_id"] == "memory_summarizer_v1" for c in calls), (
        "memory_summarizer_v1 was never invoked between retries"
    )
    # Final feedback is the hint, not the raw judge justification.
    assert "actionable_hint" not in state.feedback  # we strip schema noise
    assert "edit only the button style block" in state.feedback
    assert "stubbed FAIL" not in state.feedback
    # Attempts were recorded ordered, with verdicts.
    assert len(state.attempts) >= 2
    assert state.attempts[0]["judge_verdict"] == "FAIL"


def test_memory_summarizer_failure_keeps_legacy_feedback(tmp_path: Path, monkeypatch) -> None:
    """Agent errors must NEVER break the loop — we fall back to the legacy justification."""
    repo = _make_demo_repo(tmp_path)
    target = repo / "src" / "components" / "header.js"
    monkeypatch.chdir(tmp_path)
    _isolate_helix_paths(monkeypatch, tmp_path)
    _stub_chain_resolution(monkeypatch)
    _stub_static_checks(monkeypatch)
    monkeypatch.setattr(vloop, "run_chain", _fake_run_chain_fail(target=target))

    def fake_structured(**_kwargs):
        raise RuntimeError("synthetic summarizer failure")

    monkeypatch.setattr(
        "agenti_helix.runtime.structured_output.run_agent_structured",
        fake_structured,
    )

    task = EditTaskSpec(
        task_id="t-mem-fail",
        intent="x",
        target_file="src/components/header.js",
        acceptance_criteria="y",
        repo_path=str(repo),
        enable_memory_summarizer=True,
    )

    state = vloop.run_verification_loop(task)
    assert state.checkpoint is not None
    assert state.checkpoint.status is VerificationStatus.BLOCKED
    # Legacy feedback was retained through the retry.
    assert "Judge reported a failure" in state.feedback


# --- supreme_court_v1 ------------------------------------------------------


def _run_with_supreme_court(
    tmp_path: Path,
    monkeypatch,
    *,
    ruling: str | None,
    raises: bool = False,
    security_blocked: bool = False,
    errors: List[str] | None = None,
) -> Any:
    repo = _make_demo_repo(tmp_path)
    target = repo / "src" / "components" / "header.js"
    monkeypatch.chdir(tmp_path)
    _isolate_helix_paths(monkeypatch, tmp_path)
    _stub_chain_resolution(monkeypatch)
    _stub_static_checks(monkeypatch, security_blocked=security_blocked, errors=errors)
    monkeypatch.setattr(vloop, "run_chain", _fake_run_chain_fail(target=target))

    invocations: List[str] = []

    def fake_structured(*, agent_id: str, **_kwargs):
        invocations.append(agent_id)
        if raises:
            raise RuntimeError("synthetic supreme_court failure")
        if agent_id == "memory_summarizer_v1":
            # Not exercised in these tests — return an innocuous hint if asked.
            return {"root_cause_hypothesis": "", "actionable_hint": "h", "anti_patterns_to_avoid": []}
        assert agent_id == "supreme_court_v1"
        return {
            "ruling": ruling,
            "justification": f"ruled {ruling}",
            "evidence": ["attempt 1 failed", "attempt 2 failed"],
        }

    monkeypatch.setattr(
        "agenti_helix.runtime.structured_output.run_agent_structured",
        fake_structured,
    )

    task = EditTaskSpec(
        task_id="t-sc",
        intent="x",
        target_file="src/components/header.js",
        acceptance_criteria="y",
        repo_path=str(repo),
        enable_supreme_court=True,
    )
    return vloop.run_verification_loop(task), invocations


def test_supreme_court_pass_override_promotes_to_passed(tmp_path: Path, monkeypatch) -> None:
    state, invocations = _run_with_supreme_court(tmp_path, monkeypatch, ruling="PASS_OVERRIDE")
    assert state.checkpoint is not None
    # Patch pipeline (default) stages PASS as PASSED_PENDING_SIGNOFF, awaiting
    # manual sign-off; build pipeline would map to PASSED directly. Either is
    # a "pass" terminal status — both count as a successful override.
    assert state.checkpoint.status in (
        VerificationStatus.PASSED,
        VerificationStatus.PASSED_PENDING_SIGNOFF,
    )
    assert "supreme_court_v1" in invocations
    # Ruling surfaces in tool_logs so humans can audit the override.
    assert state.supreme_court_ruling is not None
    assert state.supreme_court_ruling["ruling"] == "PASS_OVERRIDE"


def test_supreme_court_escalate_human_blocks_with_review_flag(tmp_path: Path, monkeypatch) -> None:
    state, invocations = _run_with_supreme_court(tmp_path, monkeypatch, ruling="ESCALATE_HUMAN")
    assert state.checkpoint is not None
    assert state.checkpoint.status is VerificationStatus.BLOCKED
    assert "supreme_court_v1" in invocations
    # Tool_logs advertise that a human must review this BLOCKED verdict.
    tool_logs = state.checkpoint.tool_logs or {}
    assert tool_logs.get("human_review_required") is True
    assert (tool_logs.get("supreme_court") or {}).get("ruling") == "ESCALATE_HUMAN"


def test_supreme_court_confirm_blocked_keeps_legacy_blocked(tmp_path: Path, monkeypatch) -> None:
    state, invocations = _run_with_supreme_court(tmp_path, monkeypatch, ruling="CONFIRM_BLOCKED")
    assert state.checkpoint is not None
    assert state.checkpoint.status is VerificationStatus.BLOCKED
    assert "supreme_court_v1" in invocations
    tool_logs = state.checkpoint.tool_logs or {}
    assert tool_logs.get("human_review_required") is not True
    assert (tool_logs.get("supreme_court") or {}).get("ruling") == "CONFIRM_BLOCKED"


def test_supreme_court_failure_falls_back_to_blocked(tmp_path: Path, monkeypatch) -> None:
    """An arbitration failure must never swallow a BLOCKED — that'd be a safety regression."""
    state, invocations = _run_with_supreme_court(tmp_path, monkeypatch, ruling=None, raises=True)
    assert state.checkpoint is not None
    assert state.checkpoint.status is VerificationStatus.BLOCKED
    assert "supreme_court_v1" in invocations
    # No ruling was recorded (agent raised before a response existed).
    assert state.supreme_court_ruling is None


def test_security_blocked_bypasses_supreme_court(tmp_path: Path, monkeypatch) -> None:
    """Security findings short-circuit the loop — supreme_court must never run."""
    state, invocations = _run_with_supreme_court(
        tmp_path,
        monkeypatch,
        ruling="PASS_OVERRIDE",  # wouldn't matter even if it ran
        security_blocked=True,
        errors=["hardcoded_api_key: token"],
    )
    assert state.checkpoint is not None
    assert state.checkpoint.status is VerificationStatus.BLOCKED
    assert "supreme_court_v1" not in invocations
