"""
Composable per-node execution plan.

A ``RunPlan`` is the single source of truth for how the verification loop
runs a node. It replaces the legacy 6-value ``pipeline_mode`` enum
(``patch``, ``build``, ``product_eng``, ``diff_guard_patch``,
``secure_build_plus``, ``lint_type_gate``) with four orthogonal toggles.

Composition rules (see ``build_coder_chain`` / ``build_judge_chain``):

- ``write_tests`` switches from the patch coder/judge to the full TDD pipeline
  (sdet + coder_builder + run_tests + judge_evaluator + governor).
- ``gather_doc`` prepends a ``doc_fetcher_v1`` prefix to the coder chain so
  PRD / API constraints are merged into the per-node intent.
- ``diff_gate`` inserts the ``diff_validator_v1`` gate before the judge; on
  BLOCK it short-circuits ``judge_response`` so downstream judge steps skip.
- ``lint_type_gate`` runs ``linter_v1`` + ``type_checker_v1`` and folds the
  findings into the judge prompt (TDD pipeline only).

Two presets are exposed for the dashboard; everything else is the
"Advanced" disclosure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from agenti_helix.runtime.chain_defaults import (
    default_coder_chain,
    default_full_pipeline_coder_chain,
    default_full_pipeline_judge_chain,
    default_judge_chain,
    diff_validator_gate_steps,
    doc_prefix_steps,
    lint_type_gate_steps,
)
from agenti_helix.verification.checkpointing import EditTaskSpec


@dataclass(frozen=True)
class RunPlan:
    gather_doc: bool = False
    write_tests: bool = False
    diff_gate: bool = False
    lint_type_gate: bool = False
    # Retry-loop opt-ins (verification_loop consumes these directly via
    # EditTaskSpec; they do not affect coder / judge *chain* composition):
    memory_summarizer: bool = False  # enrich per-retry feedback with memory_summarizer_v1
    supreme_court: bool = False      # final arbitration on exhausted retries

    def to_dict(self) -> Dict[str, bool]:
        return {
            "gather_doc": self.gather_doc,
            "write_tests": self.write_tests,
            "diff_gate": self.diff_gate,
            "lint_type_gate": self.lint_type_gate,
            "memory_summarizer": self.memory_summarizer,
            "supreme_court": self.supreme_court,
        }

    @classmethod
    def from_extras(cls, mode: Optional[str], extras: Mapping[str, Any]) -> "RunPlan":
        """Translate the dashboard ``(mode, extras)`` payload into a RunPlan.

        - ``mode="patch"``  → no TDD (write_tests stays False)
        - ``mode="build"``  → TDD (write_tests=True)
        - ``mode=None``     → defer to ``write_tests`` extra (intent compiler
          can also override per node; this just sets the global default).
        """
        m = (mode or "").strip().lower()
        write_tests = m == "build" or bool(extras.get("write_tests"))
        return cls(
            gather_doc=bool(extras.get("doc") or extras.get("gather_doc")),
            write_tests=write_tests,
            diff_gate=bool(extras.get("diff_gate")),
            lint_type_gate=bool(extras.get("lint_type") or extras.get("lint_type_gate")),
            memory_summarizer=bool(extras.get("memory_summarizer")),
            supreme_court=bool(extras.get("supreme_court")),
        )


# Dashboard presets — keep these stable; the UI surfaces them as named buttons.
PRESET_QUICK_PATCH = RunPlan()
PRESET_FULL_TDD = RunPlan(gather_doc=True, write_tests=True, diff_gate=True)
# Deliberative preset: patch-style (fast single-file) + both retry agents on.
# Use when a task has been flaky in the past or when you want the strictest
# arbitration before a final BLOCKED.
PRESET_DELIBERATIVE = RunPlan(memory_summarizer=True, supreme_court=True)


# ---------------------------------------------------------------------------
# Chain composition
# ---------------------------------------------------------------------------


def _allowed_paths_ref(*, write_tests: bool) -> str:
    """Where the diff_validator should look for the allowed-paths set."""
    return "diff_json.files_written" if write_tests else "repo_map_ctx.allowed_paths"


def build_coder_chain(task: Optional[EditTaskSpec], plan: RunPlan) -> Dict[str, Any]:
    """Compose the coder chain from a RunPlan.

    Doc prefix is omitted when the doc was already merged into the macro intent
    at compile time (``task.skip_doc_chain_prefix``).
    """
    base = default_full_pipeline_coder_chain(task) if plan.write_tests else default_coder_chain(task)

    if not plan.gather_doc:
        return base
    if task is not None and getattr(task, "skip_doc_chain_prefix", False):
        return base
    return {"steps": doc_prefix_steps(intent_key="intent") + base["steps"]}


def build_judge_chain(task: Optional[EditTaskSpec], plan: RunPlan) -> Dict[str, Any]:
    """Compose the judge chain from a RunPlan.

    Layering order (each step opts out via ``skip_if_nonempty_key="judge_response"``
    once the diff gate fires):
      1. Optional diff_validator gate (sets ``judge_response`` on BLOCK).
      2. Body — TDD pipeline (run_tests + governor + evaluator + map) or
         patch-style snippet judge_v1.
      3. Optional lint_type overlay (TDD only).
    """
    if plan.write_tests:
        body = _tdd_judge_body(plan=plan)
    else:
        body = _patch_judge_body(skip_after_gate=plan.diff_gate)

    if not plan.diff_gate:
        return {"steps": body}

    # diff_validator needs `rules.repo_rules_text`; the TDD body already loads
    # rules, but the patch body does not. Hoist a load_rules step in front.
    needs_rules = not plan.write_tests
    gate = diff_validator_gate_steps(allowed_paths_ref=_allowed_paths_ref(write_tests=plan.write_tests))
    if needs_rules:
        from agenti_helix.runtime.chain_defaults import _step_load_rules  # local to avoid cycle on edits

        gate = [_step_load_rules()] + gate

    if plan.write_tests and not plan.lint_type_gate:
        # Need diff context (write_tests body uses `diff_json.files_written` from
        # write_files which has already run by the time judge starts).
        pass

    return {"steps": gate + body}


def _patch_judge_body(*, skip_after_gate: bool) -> list[Dict[str, Any]]:
    from agenti_helix.runtime.chain_defaults import (
        _step_build_tool_logs,
        _step_infer_language,
        _step_judge_v1,
        _step_snapshot_edited,
    )

    skip = "judge_response" if skip_after_gate else None
    return [
        _step_snapshot_edited(skip_key=skip),
        _step_infer_language(skip_key=skip),
        _step_build_tool_logs(skip_key=skip),
        _step_judge_v1(skip_key=skip),
    ]


def _tdd_judge_body(*, plan: RunPlan) -> list[Dict[str, Any]]:
    from agenti_helix.runtime.chain_defaults import (
        _step_infer_language,
        _step_judge_evaluator,
        _step_load_rules,
        _step_map_evaluator_verdict,
        _step_run_tests,
        _step_security_governor,
        _step_snapshot_edited,
    )

    skip = "judge_response" if plan.diff_gate else None
    steps: list[Dict[str, Any]] = [
        _step_run_tests(),
        # `load_rules` may already have been hoisted by the gate; the runtime
        # tolerates duplicate steps that overwrite the same `rules` key.
        _step_load_rules(),
    ]

    if plan.lint_type_gate:
        steps += [_step_snapshot_edited(), _step_infer_language()]
        steps += lint_type_gate_steps()

    steps += [
        _step_security_governor(),
        _step_judge_evaluator(skip_key=skip),
        _step_map_evaluator_verdict(skip_key=skip),
    ]
    return steps


# ---------------------------------------------------------------------------
# Backwards-compat: translate legacy pipeline_mode strings to RunPlans.
# Callers (master_orchestrator, tests) can keep passing pipeline_mode for now.
# ---------------------------------------------------------------------------


_LEGACY_MODE_TO_PLAN: Dict[str, RunPlan] = {
    "patch": RunPlan(),
    "diff_guard_patch": RunPlan(diff_gate=True),
    "build": RunPlan(write_tests=True),
    "secure_build_plus": RunPlan(write_tests=True, diff_gate=True),
    "product_eng": RunPlan(gather_doc=True, write_tests=True, diff_gate=True),
    "lint_type_gate": RunPlan(write_tests=True, lint_type_gate=True),
}


def plan_from_legacy_mode(mode: Optional[str]) -> RunPlan:
    return _LEGACY_MODE_TO_PLAN.get((mode or "patch").strip().lower(), RunPlan())
