#!/usr/bin/env python3
"""
Run Agenti-Helix verification once per SWE-bench-style instance and emit ``model_patch``
(``git_unified_diff`` from the final checkpoint), suitable for ``swebench.harness.run_evaluation``.

Usage (repo root; requires backend deps + LLM env as for normal runs):

  export AGENTI_HELIX_REPO_ROOT=/path/to/swe-checkout   # optional; --repo-path sets it

  python scripts/eval/swebench_adapter.py \\
    --repo-path /path/to/swe-checkout \\
    --instance-json /path/to/one_instance.json \\
    --target-file sympy/core/foo.py \\
    --output-jsonl predictions.jsonl

Each JSON instance should include at least ``instance_id`` and ``problem_statement``
(SWE-bench / HuggingFace schema). Optional: ``hints_text``, ``patch`` (gold; see below).

Target file (required unless inferred):
  --target-file PATH
  or per-instance key ``agenti_helix_target_file``
  or --infer-target-from-gold-patch (parses first ``+++ b/`` from instance["patch"]; dev-only).

Batch mode: --input-jsonl instances.jsonl --output-jsonl predictions.jsonl
  Each line must be a JSON object. If ``repo_path`` is set on a row, it wins; else --repo-path.
  Each batch row runs in a **fresh subprocess** so ``AGENTI_HELIX_REPO_ROOT`` / checkpoint paths stay
  correct when rows use different checkouts.

Predictions lines match SWE-bench: instance_id, model_name_or_path, model_patch.
Extra keys ``verification_status``, ``checkpoint_id``, ``error`` are appended for debugging;
strip them before submitting to the harness if your tooling is strict.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _backend_dir() -> Path:
    return _repo_root() / "backend"


def _ensure_backend_on_path() -> None:
    bd = _backend_dir()
    if str(bd) not in sys.path:
        sys.path.insert(0, str(bd))


def _load_instance(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"instance JSON must be an object: {path}")
    return raw


def _iter_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            out.append(row)
    return out


def _resolve_target_file(
    instance: Dict[str, Any],
    *,
    cli_target: Optional[str],
    infer_from_gold: bool,
) -> str:
    from agenti_helix.evals.swebench_utils import first_relpath_from_unified_patch

    if cli_target and cli_target.strip():
        return cli_target.strip().replace("\\", "/").lstrip("/")
    aug = instance.get("agenti_helix_target_file")
    if isinstance(aug, str) and aug.strip():
        return aug.strip().replace("\\", "/").lstrip("/")
    if infer_from_gold:
        gold = instance.get("patch")
        if isinstance(gold, str):
            p = first_relpath_from_unified_patch(gold)
            if p:
                return p.replace("\\", "/").lstrip("/")
    raise SystemExit(
        "Could not resolve target file. Pass --target-file, add "
        "'agenti_helix_target_file' to the instance JSON, or use "
        "--infer-target-from-gold-patch with a 'patch' field present."
    )


def _build_intent(instance: Dict[str, Any]) -> str:
    ps = instance.get("problem_statement") or instance.get("problem") or ""
    if not isinstance(ps, str):
        ps = str(ps)
    hints = instance.get("hints_text") or ""
    if isinstance(hints, str) and hints.strip():
        return f"{ps.strip()}\n\n## Hints\n{hints.strip()}"
    return ps.strip()


def _default_acceptance_criteria() -> str:
    return (
        "Implement a correct fix for the issue described above. "
        "Minimize unrelated edits. The change should be suitable to apply as a git patch "
        "on the repository at the task base commit."
    )


def _run_one(
    *,
    repo_path: Path,
    instance: Dict[str, Any],
    target_file: str,
    pipeline_mode: str,
    model_name: str,
) -> Tuple[str, Dict[str, Any]]:
    """Configure env, import backend, run loop. Returns (model_patch, metadata)."""
    repo = repo_path.resolve()
    os.environ["AGENTI_HELIX_REPO_ROOT"] = str(repo)

    if str(_backend_dir()) not in sys.path:
        sys.path.insert(0, str(_backend_dir()))

    from agenti_helix.verification.checkpointing import EditTaskSpec
    from agenti_helix.verification.verification_loop import run_verification_loop

    iid = str(instance.get("instance_id") or instance.get("id") or f"task-{uuid.uuid4().hex[:12]}")
    task = EditTaskSpec(
        task_id=f"swebench-{iid}",
        intent=_build_intent(instance),
        target_file=target_file,
        acceptance_criteria=_default_acceptance_criteria(),
        repo_path=str(repo),
        pipeline_mode=pipeline_mode,
    )
    trace_id = str(uuid.uuid4())
    final = run_verification_loop(task, trace_id=trace_id, dag_id=f"swebench:{iid}")

    meta: Dict[str, Any] = {
        "trace_id": trace_id,
        "verification_status": None,
        "checkpoint_id": None,
        "judge_verdict": None,
        "error": None,
    }
    cp = final.checkpoint
    if cp is None:
        meta["error"] = "no_checkpoint"
        return "", meta
    meta["verification_status"] = cp.status.value
    meta["checkpoint_id"] = cp.checkpoint_id
    jr = final.judge_response or {}
    meta["judge_verdict"] = jr.get("verdict")
    logs = cp.tool_logs or {}
    patch = str(logs.get("git_unified_diff") or "").strip()
    return patch, meta


def _prediction_record(
    instance_id: str,
    model_patch: str,
    model_name: str,
    meta: Dict[str, Any],
    *,
    slim: bool,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "instance_id": instance_id,
        "model_name_or_path": model_name,
        "model_patch": model_patch,
    }
    if not slim:
        rec.update(meta)
    return rec


def _worker_run(request_path: Path) -> int:
    """Internal: load worker request JSON, run verification, print one JSON object on stdout."""
    _ensure_backend_on_path()
    iid = "unknown_instance"
    try:
        req = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(req, dict):
            raise ValueError("worker request must be a JSON object")
        repo = Path(str(req["repo_path"])).resolve()
        instance = req["instance"]
        if not isinstance(instance, dict):
            raise ValueError("worker request 'instance' must be an object")
        iid = str(instance.get("instance_id") or instance.get("id") or "unknown_instance")
        target_file = str(req["target_file"])
        pipeline_mode = str(req.get("pipeline_mode") or "patch")
        model_name = str(req.get("model_name_or_path") or "agenti_helix")
        patch, meta = _run_one(
            repo_path=repo,
            instance=instance,
            target_file=target_file,
            pipeline_mode=pipeline_mode,
            model_name=model_name,
        )
        print(json.dumps({"instance_id": iid, "model_patch": patch, "meta": meta}, ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "instance_id": iid,
                    "model_patch": "",
                    "meta": {"error": f"{type(exc).__name__}: {exc}"},
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


def _invoke_worker(req: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """Run ``_worker_run`` in a subprocess; return (instance_id, model_patch, meta)."""
    script = Path(__file__).resolve()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(req, tmp, ensure_ascii=False)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--worker-request", tmp_path],
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=None,
        )
        lines = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
        if not lines:
            err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            return "unknown_instance", "", {"error": f"worker_empty_stdout: {err}"}
        data = json.loads(lines[-1])
        if not isinstance(data, dict):
            return "unknown_instance", "", {"error": "worker_invalid_json"}
        iid = str(data.get("instance_id") or "unknown_instance")
        patch = str(data.get("model_patch") or "")
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        return iid, patch, dict(meta)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate SWE-bench predictions via Agenti-Helix verification loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo-path", type=str, default="", help="Git checkout root (SWE-bench instance repo)")
    parser.add_argument("--instance-json", type=str, default="", help="Path to one instance JSON object")
    parser.add_argument("--input-jsonl", type=str, default="", help="Batch: JSONL of instances")
    parser.add_argument(
        "--output-jsonl",
        type=str,
        default="",
        help="Append predictions (JSONL); not used with --worker-request",
    )
    parser.add_argument(
        "--worker-request",
        type=str,
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--target-file", type=str, default="", help="Repo-relative primary file to edit")
    parser.add_argument(
        "--infer-target-from-gold-patch",
        action="store_true",
        help="Infer --target-file from instance['patch'] (+++ b/...); dev/scaffolding only.",
    )
    parser.add_argument(
        "--pipeline-mode",
        type=str,
        default="patch",
        help="EditTaskSpec.pipeline_mode (default patch; use build for multi-file TDD-style)",
    )
    parser.add_argument(
        "--model-name-or-path",
        type=str,
        default="agenti_helix",
        help="Written to model_name_or_path in predictions",
    )
    parser.add_argument(
        "--slim-predictions",
        action="store_true",
        help="Emit only instance_id, model_name_or_path, model_patch (strict SWE-bench schema)",
    )
    args = parser.parse_args()

    if args.worker_request.strip():
        return _worker_run(Path(args.worker_request).resolve())

    if not args.output_jsonl.strip():
        parser.error("--output-jsonl is required (unless using --worker-request)")

    _ensure_backend_on_path()

    out_path = Path(args.output_jsonl).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    runs: List[Tuple[Path, Dict[str, Any]]] = []

    if args.input_jsonl:
        in_path = Path(args.input_jsonl).resolve()
        if not in_path.is_file():
            raise SystemExit(f"input-jsonl not found: {in_path}")
        default_repo = Path(args.repo_path).resolve() if args.repo_path.strip() else None
        for row in _iter_jsonl(in_path):
            rp = row.get("repo_path")
            if isinstance(rp, str) and rp.strip():
                repo = Path(rp).resolve()
            elif default_repo is not None:
                repo = default_repo
            else:
                raise SystemExit("Each JSONL row needs repo_path or provide global --repo-path")
            runs.append((repo, row))
    elif args.instance_json:
        if not args.repo_path.strip():
            raise SystemExit("--repo-path is required with --instance-json")
        runs.append((Path(args.repo_path).resolve(), _load_instance(Path(args.instance_json).resolve())))
    else:
        raise SystemExit("Provide --instance-json or --input-jsonl")

    use_subprocess = bool(args.input_jsonl)

    with out_path.open("a", encoding="utf-8") as fh:
        for repo, instance in runs:
            if not repo.is_dir():
                raise SystemExit(f"repo-path is not a directory: {repo}")
            tf = _resolve_target_file(
                instance,
                cli_target=args.target_file or None,
                infer_from_gold=args.infer_target_from_gold_patch,
            )
            iid = str(instance.get("instance_id") or instance.get("id") or "unknown_instance")
            try:
                if use_subprocess:
                    iid, patch, meta = _invoke_worker(
                        {
                            "repo_path": str(repo),
                            "instance": instance,
                            "target_file": tf,
                            "pipeline_mode": args.pipeline_mode.strip() or "patch",
                            "model_name_or_path": args.model_name_or_path,
                        }
                    )
                else:
                    patch, meta = _run_one(
                        repo_path=repo,
                        instance=instance,
                        target_file=tf,
                        pipeline_mode=args.pipeline_mode.strip() or "patch",
                        model_name=args.model_name_or_path,
                    )
            except Exception as exc:  # noqa: BLE001 — surface batch errors per row
                meta = {"error": f"{type(exc).__name__}: {exc}", "trace_id": None}
                patch = ""
            rec = _prediction_record(iid, patch, args.model_name_or_path, meta, slim=args.slim_predictions)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()

    print(f"Wrote predictions to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
