"""Unit tests for the composable RunPlan + chain builders."""
from __future__ import annotations

import pytest

from agenti_helix.runtime.run_plan import (
    PRESET_DELIBERATIVE,
    PRESET_FULL_TDD,
    PRESET_QUICK_PATCH,
    RunPlan,
    build_coder_chain,
    build_judge_chain,
    plan_from_legacy_mode,
)
from agenti_helix.verification.checkpointing import EditTaskSpec


def _task(mode: str = "patch") -> EditTaskSpec:
    return EditTaskSpec(
        task_id="t",
        intent="i",
        target_file="src/x.js",
        acceptance_criteria="ok",
        repo_path="/tmp/repo",
        pipeline_mode=mode,
    )


def _ids(chain: dict) -> list[str]:
    return [s["id"] for s in chain["steps"]]


# ─── Plan-from-mode round-trip ────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("patch", RunPlan()),
        ("diff_guard_patch", RunPlan(diff_gate=True)),
        ("build", RunPlan(write_tests=True)),
        ("secure_build_plus", RunPlan(write_tests=True, diff_gate=True)),
        ("product_eng", RunPlan(gather_doc=True, write_tests=True, diff_gate=True)),
        ("lint_type_gate", RunPlan(write_tests=True, lint_type_gate=True)),
        ("UNKNOWN", RunPlan()),  # falls back to defaults
    ],
)
def test_plan_from_legacy_mode(mode: str, expected: RunPlan) -> None:
    assert plan_from_legacy_mode(mode) == expected


# ─── Coder chain composition ──────────────────────────────────────────────


def test_quick_patch_coder_is_focused_repo_map_chain() -> None:
    chain = build_coder_chain(_task(), PRESET_QUICK_PATCH)
    assert _ids(chain) == ["build_repo_map_ctx", "snapshot_target", "coder_patch", "apply_patch"]


def test_full_tdd_coder_has_doc_prefix_then_tdd_body() -> None:
    chain = build_coder_chain(_task(), PRESET_FULL_TDD)
    assert _ids(chain)[:3] == ["fetch_doc", "doc_fetcher", "merge_doc_intent"]
    assert "sdet" in _ids(chain)
    assert "coder_builder" in _ids(chain)
    assert _ids(chain)[-1] == "write_files"


def test_skip_doc_chain_prefix_drops_doc_block() -> None:
    """When the doc was merged at compile time, per-node coder skips the prefix."""
    task = _task("product_eng")
    task.skip_doc_chain_prefix = True
    chain = build_coder_chain(task, PRESET_FULL_TDD)
    assert "fetch_doc" not in _ids(chain)
    assert "doc_fetcher" not in _ids(chain)


# ─── Judge chain composition ──────────────────────────────────────────────


def test_quick_patch_judge_is_snippet_judge_chain() -> None:
    chain = build_judge_chain(_task(), PRESET_QUICK_PATCH)
    assert _ids(chain) == ["snapshot_edited", "infer_language", "build_tool_logs_json", "judge"]


def test_diff_gate_inserts_validator_before_judge() -> None:
    plan = RunPlan(diff_gate=True)
    chain = build_judge_chain(_task(), plan)
    ids = _ids(chain)
    assert ids.index("load_rules") < ids.index("diff_validator")
    assert "diff_validator" in ids
    assert ids.index("diff_validator") < ids.index("judge")
    # On BLOCK, the snippet steps must short-circuit on judge_response.
    snippet_step = next(s for s in chain["steps"] if s["id"] == "snapshot_edited")
    assert snippet_step.get("skip_if_nonempty_key") == "judge_response"


def test_full_tdd_judge_has_run_tests_and_evaluator() -> None:
    chain = build_judge_chain(_task(), RunPlan(write_tests=True))
    ids = _ids(chain)
    assert "run_tests" in ids
    assert "judge_evaluator" in ids
    assert "map_verdict" in ids


def test_full_tdd_preset_loads_rules_before_diff_gate() -> None:
    """PRESET_FULL_TDD enables diff_gate; diff_validator must not resolve refs before rules exist."""
    chain = build_judge_chain(_task(), PRESET_FULL_TDD)
    ids = _ids(chain)
    assert ids.index("load_rules") < ids.index("diff_validator")
    assert ids.index("diff_validator") < ids.index("run_tests")


def test_lint_type_gate_overlays_linter_and_typechecker() -> None:
    chain = build_judge_chain(_task(), RunPlan(write_tests=True, lint_type_gate=True))
    ids = _ids(chain)
    for step in ("run_linter_tool", "linter_agent", "run_typecheck_tool", "type_checker_agent", "overlay_logs"):
        assert step in ids, f"missing {step} in lint_type_gate judge chain"


# ─── master_orchestrator delegates to RunPlan ─────────────────────────────


# ─── Retry-loop flags are orthogonal to chain composition ────────────────


def test_retry_flags_do_not_change_coder_chain_shape() -> None:
    """memory_summarizer / supreme_court are loop-level, not chain-level.

    Enabling them must produce the same coder chain as the equivalent plan
    with the flags off — otherwise we'd be accidentally re-entangling them
    with chain composition.
    """
    base = RunPlan()
    with_retry = RunPlan(memory_summarizer=True, supreme_court=True)
    assert _ids(build_coder_chain(_task(), base)) == _ids(build_coder_chain(_task(), with_retry))
    assert _ids(build_judge_chain(_task(), base)) == _ids(build_judge_chain(_task(), with_retry))


def test_preset_deliberative_enables_both_retry_flags() -> None:
    assert PRESET_DELIBERATIVE.memory_summarizer is True
    assert PRESET_DELIBERATIVE.supreme_court is True
    # And does NOT silently turn on TDD / doc-fetch.
    assert PRESET_DELIBERATIVE.write_tests is False
    assert PRESET_DELIBERATIVE.gather_doc is False


def test_run_plan_from_extras_threads_retry_flags() -> None:
    plan = RunPlan.from_extras(
        "patch",
        {"memory_summarizer": True, "supreme_court": True, "diff_gate": False},
    )
    assert plan.memory_summarizer is True
    assert plan.supreme_court is True
    assert plan.diff_gate is False


# ─── master_orchestrator delegates to RunPlan ─────────────────────────────


def test_master_orchestrator_uses_runplan() -> None:
    """resolve_*_chain should produce the same chain as build_*_chain via RunPlan."""
    from agenti_helix.orchestration.master_orchestrator import (
        resolve_coder_chain,
        resolve_judge_chain,
    )

    task = _task("product_eng")
    plan = plan_from_legacy_mode("product_eng")

    assert _ids(resolve_coder_chain(task)) == _ids(build_coder_chain(task, plan))
    assert _ids(resolve_judge_chain(task)) == _ids(build_judge_chain(task, plan))
