from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification import verification_loop as vloop


class DummyJudgeClient:
    def __init__(self, verdict: str) -> None:
        self._verdict = verdict

    def evaluate(self, request: Any) -> Any:
        class Resp:
            def __init__(self, verdict: str) -> None:
                self.verdict = verdict
                self.justification = "dummy"
                self.problematic_lines: list[int] = []

        return Resp(self._verdict)


def _make_demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    header = src / "header.js"
    header.write_text(
        '<button style={{ backgroundColor: "blue" }}>Click me</button>\n'
    )
    return repo


def test_verification_loop_passes_when_judge_passes(tmp_path: Path, monkeypatch) -> None:
    repo = _make_demo_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Monkeypatch single_agent_harness.run_single_agent_edit to apply a deterministic change.
    def fake_run_single_agent_edit(repo_root: Path, intent: str) -> Any:  # type: ignore[override]
        target = repo_root / "src" / "components" / "header.js"
        target.write_text(
            '<button style={{ backgroundColor: "green" }}>Click me</button>\n'
        )

        class Patch:
            def __init__(self) -> None:
                self.file_path = "src/components/header.js"
                self.start_line = 1
                self.end_line = 1
                self.replacement_lines = [
                    '<button style={{ backgroundColor: "green" }}>Click me</button>'
                ]

        return Patch()

    monkeypatch.setattr(vloop, "run_single_agent_edit", fake_run_single_agent_edit)
    monkeypatch.setattr(vloop, "JudgeClient", lambda *args, **kwargs: DummyJudgeClient("PASS"))

    task = EditTaskSpec(
        task_id="t-pass",
        intent="Change header button color",
        target_file="src/components/header.js",
        acceptance_criteria="button color is green",
        repo_path=str(repo),
    )

    final_state = vloop.run_verification_loop(task)
    assert final_state.checkpoint is not None
    assert final_state.checkpoint.status is VerificationStatus.PASSED


def test_verification_loop_blocks_after_retries(tmp_path: Path, monkeypatch) -> None:
    repo = _make_demo_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_run_single_agent_edit(repo_root: Path, intent: str) -> Any:  # type: ignore[override]
        # Intentionally write a bad change the Judge will always fail.
        target = repo_root / "src" / "components" / "header.js"
        target.write_text("BROKEN CODE\n")

        class Patch:
            def __init__(self) -> None:
                self.file_path = "src/components/header.js"
                self.start_line = 1
                self.end_line = 1
                self.replacement_lines = ["BROKEN CODE"]

        return Patch()

    monkeypatch.setattr(vloop, "run_single_agent_edit", fake_run_single_agent_edit)

    # Force Judge to always FAIL.
    monkeypatch.setattr(vloop, "JudgeClient", lambda *args, **kwargs: DummyJudgeClient("FAIL"))

    task = EditTaskSpec(
        task_id="t-fail",
        intent="Break header",
        target_file="src/components/header.js",
        acceptance_criteria="button remains valid",
        repo_path=str(repo),
    )

    final_state = vloop.run_verification_loop(task)
    assert final_state.checkpoint is not None
    # After exhausting retries, we expect the checkpoint to be BLOCKED.
    assert final_state.checkpoint.status in {VerificationStatus.BLOCKED, VerificationStatus.FAILED}

