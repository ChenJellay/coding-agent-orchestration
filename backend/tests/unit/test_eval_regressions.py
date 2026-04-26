"""Regression tests for headless eval failure cases (S5/S6).

These tests lock down:
- Cascade failure persistence in orchestrator (dependents marked FAILED are persisted).
- Bandit security parsing is robust (HIGH/HIGH findings trigger security_blocked path).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agenti_helix.api.paths import HelixPaths
from agenti_helix.orchestration import orchestrator as orch
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification import verification_loop as vloop


def _isolate_paths(monkeypatch, tmp_path: Path) -> HelixPaths:
    paths = HelixPaths(repo_root=tmp_path)
    # Patch both modules that import PATHS.
    monkeypatch.setattr("agenti_helix.api.paths.PATHS", paths)
    monkeypatch.setattr("agenti_helix.orchestration.orchestrator.PATHS", paths)
    monkeypatch.setattr("agenti_helix.verification.checkpointing.PATHS", paths)
    return paths


def test_orchestrator_persists_cascade_failed_nodes(tmp_path: Path, monkeypatch) -> None:
    """When a predecessor fails, dependents are marked FAILED and persisted (not left PENDING)."""
    paths = _isolate_paths(monkeypatch, tmp_path)

    repo = tmp_path / "demo"
    repo.mkdir()

    called: List[str] = []

    def fake_run_verification_loop(task: EditTaskSpec, **_kwargs: Any) -> Any:
        # Fail N1 deterministically.
        called.append(task.task_id)
        cp = SimpleNamespace(status=VerificationStatus.BLOCKED)
        return SimpleNamespace(checkpoint=cp)

    monkeypatch.setattr(orch, "run_verification_loop", fake_run_verification_loop)

    dag_id = "t-cascade"
    n1 = orch.DagNodeSpec(
        node_id="N1",
        description="fail",
        task=EditTaskSpec(
            task_id=f"{dag_id}:N1",
            intent="x",
            target_file="missing.txt",
            acceptance_criteria="x",
            repo_path=str(repo),
        ),
    )
    n2 = orch.DagNodeSpec(
        node_id="N2",
        description="dependent",
        task=EditTaskSpec(
            task_id=f"{dag_id}:N2",
            intent="y",
            target_file="also_missing.txt",
            acceptance_criteria="y",
            repo_path=str(repo),
        ),
    )
    spec = orch.DagSpec(dag_id=dag_id, macro_intent="m", nodes={"N1": n1, "N2": n2}, edges=[("N1", "N2")])

    orch.execute_dag(spec)

    # Only N1 ran verification; N2 was cascade-failed.
    assert called == [f"{dag_id}:N1"]

    state_path = paths.dags_dir / f"{dag_id}_state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    nodes = raw.get("nodes") or {}
    assert (nodes.get("N2") or {}).get("status") == orch.DagNodeStatus.FAILED.value


def test_bandit_security_uses_json_and_finds_high_high(monkeypatch, tmp_path: Path) -> None:
    """_check_bandit_security should parse bandit JSON output and emit [SECURITY] errors."""
    target = tmp_path / "x.py"
    target.write_text("import subprocess\nsubprocess.call('x', shell=True)\n", encoding="utf-8")

    payload: Dict[str, Any] = {
        "results": [
            {
                "test_id": "B602",
                "issue_text": "subprocess call with shell=True",
                "issue_severity": "HIGH",
                "issue_confidence": "HIGH",
                "filename": str(target),
                "line_number": 2,
            }
        ]
    }

    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=1, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(vloop.subprocess, "run", fake_run)

    errs = vloop._check_bandit_security(target)
    assert errs, "expected at least one security finding"
    assert any(e.startswith("[SECURITY]") and "B602" in e for e in errs)

