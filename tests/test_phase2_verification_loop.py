"""End-to-end smoke tests for the simplified verification loop.

The loop's external contract is:
  pre_checkpoint -> coder chain -> static checks -> judge chain -> verdict.

These tests stub `run_chain` (which the loop calls for both the coder and the
judge), `resolve_coder_chain` / `resolve_judge_chain` (looked up lazily inside
the loop), and `_run_static_checks` (which would otherwise shell out to
`node --check` and `bandit`). With those stubs in place we can drive both the
PASS and BLOCKED-after-retries paths without a model or a sandbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenti_helix.api.paths import HelixPaths
from agenti_helix.verification import checkpointing as cp_mod
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification import verification_loop as vloop


def _isolate_helix_paths(monkeypatch, tmp_path: Path) -> None:
    """Redirect checkpoint storage to ``tmp_path`` so tests don't pollute the workspace."""
    monkeypatch.setattr(cp_mod, "PATHS", HelixPaths(repo_root=tmp_path))


def _make_demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    header = src / "header.js"
    header.write_text(
        '<button style={{ backgroundColor: "blue" }}>Click me</button>\n'
    )
    return repo


def _stub_chain_resolution(monkeypatch) -> None:
    """Bypass the lazy master_orchestrator imports in `_run_coder` / `_call_judge`."""
    monkeypatch.setattr(
        "agenti_helix.orchestration.master_orchestrator.resolve_coder_chain",
        lambda task: {"steps": []},
    )
    monkeypatch.setattr(
        "agenti_helix.orchestration.master_orchestrator.resolve_judge_chain",
        lambda task: {"steps": []},
    )


def _stub_static_checks(monkeypatch) -> None:
    """Skip subprocess calls to ruff/node/bandit; treat everything as clean."""
    monkeypatch.setattr(
        vloop,
        "_run_static_checks",
        lambda repo_root, target_file: {
            "passed": True,
            "errors": [],
            "checks_run": ["stub"],
            "security_blocked": False,
        },
    )


def _make_fake_run_chain(*, write_target: Path, write_body: str, judge_verdict: str):
    """Build a `run_chain` replacement that simulates the coder writing a file
    and the judge returning the given verdict."""

    def fake_run_chain(*, chain_spec, initial_context, location_prefix, **_kwargs):
        ctx = dict(initial_context)
        if "_run_coder" in location_prefix:
            write_target.write_text(write_body)
            ctx["coder_patch"] = {
                "filePath": "src/components/header.js",
                "startLine": 1,
                "endLine": 1,
                "replacementLines": [write_body.rstrip("\n")],
            }
            ctx["diff_json"] = {"filePath": "src/components/header.js"}
        elif "_call_judge" in location_prefix:
            ctx["judge_response"] = {
                "verdict": judge_verdict,
                "justification": f"stubbed {judge_verdict}",
                "problematic_lines": [],
            }
        return ctx

    return fake_run_chain


def test_verification_loop_passes_when_judge_passes(tmp_path: Path, monkeypatch) -> None:
    repo = _make_demo_repo(tmp_path)
    target = repo / "src" / "components" / "header.js"
    monkeypatch.chdir(tmp_path)

    _isolate_helix_paths(monkeypatch, tmp_path)
    _stub_chain_resolution(monkeypatch)
    _stub_static_checks(monkeypatch)
    monkeypatch.setattr(
        vloop,
        "run_chain",
        _make_fake_run_chain(
            write_target=target,
            write_body='<button style={{ backgroundColor: "green" }}>Click me</button>\n',
            judge_verdict="PASS",
        ),
    )

    task = EditTaskSpec(
        task_id="t-pass",
        intent="Change header button color",
        target_file="src/components/header.js",
        acceptance_criteria="button color is green",
        repo_path=str(repo),
        pipeline_mode="build",  # PASSED (not PASSED_PENDING_SIGNOFF) on judge PASS.
    )

    final_state = vloop.run_verification_loop(task)
    assert final_state.checkpoint is not None
    assert final_state.checkpoint.status is VerificationStatus.PASSED


def test_verification_loop_blocks_after_retries(tmp_path: Path, monkeypatch) -> None:
    repo = _make_demo_repo(tmp_path)
    target = repo / "src" / "components" / "header.js"
    monkeypatch.chdir(tmp_path)

    _isolate_helix_paths(monkeypatch, tmp_path)
    _stub_chain_resolution(monkeypatch)
    _stub_static_checks(monkeypatch)
    monkeypatch.setattr(
        vloop,
        "run_chain",
        _make_fake_run_chain(
            write_target=target,
            write_body="BROKEN CODE\n",
            judge_verdict="FAIL",
        ),
    )

    task = EditTaskSpec(
        task_id="t-fail",
        intent="Break header",
        target_file="src/components/header.js",
        acceptance_criteria="button remains valid",
        repo_path=str(repo),
    )

    final_state = vloop.run_verification_loop(task)
    assert final_state.checkpoint is not None
    assert final_state.checkpoint.status is VerificationStatus.BLOCKED
    assert final_state.retry_count >= 2  # max_retries = 2
