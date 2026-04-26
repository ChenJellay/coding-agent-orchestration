#!/usr/bin/env python3
"""
Headless evaluation framework: scenarios from demo-repo/eval/scenarios.json,
POST /api/dags/run or fixture DAG + POST /api/dags/{id}/resume, poll, assert, rubric + reports.

Reports (under .gitignored .agenti_helix/eval/):
  last-run.json, last-run.md

Usage (repo root):
  python scripts/eval/headless_eval.py
  python scripts/eval/headless_eval.py --tags stable
  python scripts/eval/headless_eval.py --tags stable,llm
  python scripts/eval/headless_eval.py --tags all
  python scripts/eval/headless_eval.py --scenario s1_header_green
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import httpx

IN_PROGRESS_COLUMNS = frozenset({"SCOPING", "ORCHESTRATING", "VERIFYING"})
COMPILE_FAILED = "Intent compile failed"
LOOP_START = "Starting verification loop"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_demo_repo() -> Path:
    return _repo_root() / "demo-repo"


def _events_path(repo_path: Path) -> Path:
    return (repo_path / ".agenti_helix" / "logs" / "events.jsonl").resolve()


def _eval_out_dir(repo_path: Path) -> Path:
    d = repo_path / ".agenti_helix" / "eval"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_json_path(repo_path: Path) -> Path:
    return _eval_out_dir(repo_path) / "last-run.json"


def _report_md_path(repo_path: Path) -> Path:
    return _eval_out_dir(repo_path) / "last-run.md"


def load_scenarios(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "scenarios" not in raw:
        raise ValueError("scenarios file must be a JSON object with a 'scenarios' array")
    return raw


def event_matches_dag(ev: Dict[str, Any], dag_id: str) -> bool:
    if ev.get("dagId") == dag_id:
        return True
    rid = ev.get("runId")
    if rid == dag_id:
        return True
    if isinstance(rid, str) and rid.startswith(f"{dag_id}:"):
        return True
    data = ev.get("data")
    if isinstance(data, dict) and data.get("dag_id") == dag_id:
        return True
    return False


def read_events_for_dag(repo_path: Path, dag_id: str) -> List[Dict[str, Any]]:
    path = _events_path(repo_path)
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and event_matches_dag(ev, dag_id):
            out.append(ev)
    return out


def _node_suffix_from_run_id(run_id: Any) -> Optional[str]:
    if not isinstance(run_id, str) or ":" not in run_id:
        return None
    return run_id.rsplit(":", 1)[-1]


def count_verification_loop_starts(events: List[Dict[str, Any]], dag_id: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ev in events:
        if ev.get("message") != LOOP_START:
            continue
        rid = ev.get("runId")
        if isinstance(rid, str) and rid.startswith(f"{dag_id}:"):
            node = _node_suffix_from_run_id(rid)
            if node:
                counts[node] = counts.get(node, 0) + 1
    return counts


def _auth_headers() -> Dict[str, str]:
    key = os.environ.get("AGENTI_HELIX_API_KEY", "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def delete_feature(client: httpx.Client, api_base: str, dag_id: str) -> Tuple[bool, str]:
    r = client.delete(f"{api_base}/api/features/{dag_id}")
    if r.status_code == 404:
        return True, "absent"
    if r.status_code in (200, 204):
        return True, "deleted"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def post_dag_run(
    client: httpx.Client,
    api_base: str,
    *,
    repo_path: Path,
    macro_intent: str,
    dag_id: str,
    mode: Optional[str],
    extras: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    body: Dict[str, Any] = {
        "repo_path": str(repo_path.resolve()),
        "macro_intent": macro_intent,
        "dag_id": dag_id,
    }
    if mode is not None:
        body["mode"] = mode
    if extras:
        body["extras"] = extras
    r = client.post(f"{api_base}/api/dags/run", json=body)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:500]}"
    data = r.json()
    if not data.get("ok"):
        return False, str(data)
    return True, "ok"


def post_dag_resume(client: httpx.Client, api_base: str, dag_id: str) -> Tuple[bool, str]:
    r = client.post(f"{api_base}/api/dags/{dag_id}/resume")
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:500]}"
    data = r.json()
    if not data.get("ok"):
        return False, str(data)
    return True, "ok"


def poll_feature_column(
    client: httpx.Client,
    api_base: str,
    dag_id: str,
    repo_path: Path,
    deadline: float,
) -> Tuple[str, str]:
    while time.time() < deadline:
        r = client.get(f"{api_base}/api/features/{dag_id}")
        if r.status_code == 404:
            evs = read_events_for_dag(repo_path, dag_id)
            if any(
                COMPILE_FAILED in str(ev.get("message", "")) or "Intent compile failed" in str(ev.get("message", ""))
                for ev in evs
            ):
                return "COMPILE_FAILED", "compile_failed"
            time.sleep(1.0)
            continue
        if r.status_code != 200:
            time.sleep(1.0)
            continue
        data = r.json()
        metrics = data.get("metrics") or {}
        col = metrics.get("column") or "ORCHESTRATING"
        if col not in IN_PROGRESS_COLUMNS:
            return str(col), "ok"
        time.sleep(1.0)

    return "TIMEOUT", "timeout"


def fetch_dag_state(client: httpx.Client, api_base: str, dag_id: str) -> Optional[Dict[str, Any]]:
    r = client.get(f"{api_base}/api/dags/{dag_id}/state")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            return data
    r2 = client.get(f"{api_base}/api/features/{dag_id}")
    if r2.status_code != 200:
        return None
    body = r2.json()
    st = body.get("state") if isinstance(body, dict) else None
    return st if isinstance(st, dict) else None


def fetch_triage(client: httpx.Client, api_base: str) -> List[Dict[str, Any]]:
    r = client.get(f"{api_base}/api/triage")
    if r.status_code != 200:
        return []
    data = r.json()
    items = data.get("items") if isinstance(data, dict) else None
    return list(items) if isinstance(items, list) else []


def install_dag_fixture(repo_path: Path, fixture_rel: str, dag_id: str) -> Tuple[bool, str]:
    root = repo_path.resolve()
    src = (root / fixture_rel).resolve()
    if not src.is_file():
        return False, f"fixture not found: {src}"
    try:
        src.relative_to(root)
    except ValueError:
        return False, "fixture path escapes repo"
    raw = src.read_text(encoding="utf-8")
    raw = raw.replace("__REPO_ROOT__", str(root))
    dest_dir = root / ".agenti_helix" / "dags"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{dag_id}.json"
    dest.write_text(raw, encoding="utf-8")
    state_path = dest_dir / f"{dag_id}_state.json"
    if state_path.exists():
        state_path.unlink()
    return True, str(dest)


def reset_paths_from_baseline(repo_path: Path, rel_paths: Sequence[str]) -> List[str]:
    """Copy eval/fixtures/<name>_baseline.<ext> over repo paths when baseline exists."""
    errors: List[str] = []
    root = repo_path.resolve()
    for rel in rel_paths:
        rel = rel.replace("\\", "/").lstrip("/")
        target = root / rel
        stem = target.stem
        suffix = target.suffix
        baseline_name = f"{stem}_baseline{suffix}"
        baseline = root / "eval" / "fixtures" / baseline_name
        if not baseline.is_file():
            errors.append(f"no baseline for reset_paths entry {rel!r} (expected {baseline})")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline, target)
        except OSError as exc:
            errors.append(f"reset {rel!r}: {exc}")
    return errors


def scenario_matches_tags(scenario: Dict[str, Any], tag_filter: Set[str]) -> bool:
    if tag_filter == {"all"}:
        return True
    tags = set(scenario.get("tags") or ["stable"])
    return bool(tags & tag_filter)


def check_expectations(
    column: str,
    events: List[Dict[str, Any]],
    expect: Dict[str, Any],
    *,
    elapsed_sec: float,
    dag_state: Optional[Dict[str, Any]],
    triage_items: List[Dict[str, Any]],
    dag_id: str,
) -> List[str]:
    errors: List[str] = []
    # Many important signals (e.g. bandit findings) live under ev["data"].
    # For matching expectations, consider the whole event payload, not only `message`.
    def _ev_text(ev: Dict[str, Any]) -> str:
        try:
            return json.dumps(ev, ensure_ascii=False)
        except Exception:
            return str(ev)

    exp_col = expect.get("column")
    if exp_col and column != exp_col:
        errors.append(f"column: got {column!r}, want {exp_col!r}")

    cols_in = expect.get("column_in")
    if isinstance(cols_in, list) and cols_in and column not in cols_in:
        errors.append(f"column: got {column!r}, want one of {cols_in!r}")

    for sub in expect.get("events_contain") or []:
        if not any(sub in _ev_text(ev) for ev in events):
            errors.append(f"missing event message containing: {sub!r}")

    for sub in expect.get("events_forbid") or []:
        if any(sub in _ev_text(ev) for ev in events):
            errors.append(f"forbidden event message appeared: {sub!r}")

    any_subs = expect.get("events_contain_any")
    if isinstance(any_subs, list) and any_subs:
        if not any(
            sub in _ev_text(ev)
            for ev in events
            for sub in any_subs
            if isinstance(sub, str) and sub
        ):
            errors.append(f"none of events_contain_any matched: {any_subs!r}")

    max_sec = expect.get("max_elapsed_sec")
    if max_sec is not None and elapsed_sec > float(max_sec):
        errors.append(f"SLA: elapsed {elapsed_sec:.1f}s > max_elapsed_sec={max_sec}")

    if expect.get("events_have_trace_id"):
        found = False
        for ev in events:
            if ev.get("message") == "Starting DAG execution" and ev.get("runId") == dag_id:
                found = True
                if not ev.get("traceId"):
                    errors.append("Starting DAG execution event missing traceId")
                break
        if not found:
            errors.append("no Starting DAG execution event for traceId check")

    vloop_max = expect.get("verification_loop_max_by_node")
    if isinstance(vloop_max, dict):
        counts = count_verification_loop_starts(events, dag_id)
        for node_id, max_allowed in vloop_max.items():
            n = int(counts.get(str(node_id), 0))
            ma = int(max_allowed)
            if n > ma:
                errors.append(f"verification loop starts for {node_id}: {n} > max {ma}")

    want_nodes = expect.get("state_nodes")
    if isinstance(want_nodes, list) and want_nodes:
        if not isinstance(dag_state, dict):
            errors.append("state_nodes expect: could not load /api/dags/.../state")
        else:
            nodes = dag_state.get("nodes") or {}
            for rule in want_nodes:
                if not isinstance(rule, dict):
                    continue
                nid = str(rule.get("node_id", ""))
                want_status = rule.get("status")
                raw = nodes.get(nid) or nodes.get(str(nid))
                if not isinstance(raw, dict):
                    errors.append(f"state node {nid!r}: missing")
                    continue
                got = raw.get("status")
                if want_status is not None and got != want_status:
                    errors.append(f"state node {nid!r}: status got {got!r}, want {want_status!r}")

    if expect.get("triage_lists_dag_id"):
        if not any(str(it.get("dag_id") or it.get("feature_id")) == dag_id for it in triage_items):
            errors.append(f"triage: no item for dag_id={dag_id!r}")

    return errors


def apply_rubric(
    scenario: Dict[str, Any], passed: bool, errors: List[str], bundle: Dict[str, Any]
) -> Dict[str, Any]:
    dims = bundle.get("dimensions") or []
    mapping = scenario.get("rubric_map") or {}
    out: Dict[str, Any] = {}
    for d in dims:
        if not isinstance(d, dict):
            continue
        did = str(d.get("id", ""))
        if not did:
            continue
        mode = mapping.get(did, "inherit")
        if mode == "na":
            out[did] = "na"
        elif mode == "focus":
            out[did] = "pass" if passed else "fail"
        else:
            out[did] = "pass" if passed else "fail"
    return {"by_dimension": out, "scenario_pass": passed, "errors": errors}


def write_report_markdown(
    path: Path,
    *,
    api_base: str,
    repo_path: Path,
    tag_filter: Set[str],
    bundle: Dict[str, Any],
    results: List[Dict[str, Any]],
    passed_all: bool,
) -> None:
    lines: List[str] = []
    lines.append("# Agenti-Helix headless eval report")
    lines.append("")
    lines.append(f"- **API:** `{api_base}`")
    lines.append(f"- **Repo:** `{repo_path}`")
    lines.append(f"- **Tag filter:** `{', '.join(sorted(tag_filter))}`")
    lines.append(f"- **Overall:** {'**PASS**' if passed_all else '**FAIL**'}")
    lines.append("")
    exec_sum = bundle.get("executive_summary_template") or (
        "Automated headless run against live control plane and Judge. "
        "See JSON for machine-readable details and per-check errors."
    )
    lines.append("## Executive summary")
    lines.append("")
    lines.append(exec_sum)
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Scenario | Result | Column | Seconds | Notes |")
    lines.append("|----------|--------|--------|---------|-------|")
    for r in results:
        sid = r.get("id", "?")
        if r.get("skipped"):
            row = [sid, "SKIP", "-", "-", str(r.get("skip_reason", ""))[:80]]
        else:
            st = "PASS" if r.get("passed") else "FAIL"
            errs = r.get("errors") or []
            note = "; ".join(errs)[:120] if errs else ""
            row = [sid, st, str(r.get("column")), str(r.get("elapsed_sec")), note]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## Appendix")
    lines.append("")
    lines.append(f"- JSON report: `{_report_json_path(repo_path)}`")
    lines.append(f"- Events: `{_events_path(repo_path)}`")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_one_scenario(
    client: httpx.Client,
    api_base: str,
    repo_path: Path,
    scenario: Dict[str, Any],
    defaults: Dict[str, Any],
    bundle: Dict[str, Any],
) -> Dict[str, Any]:
    sid = scenario.get("id", "?")
    t0 = time.time()

    if scenario.get("skip"):
        return {
            "id": sid,
            "dag_id": scenario.get("dag_id"),
            "skipped": True,
            "passed": True,
            "skip_reason": scenario.get("skip_reason", "skipped"),
            "elapsed_sec": round(time.time() - t0, 3),
            "rubric": apply_rubric(scenario, True, [], bundle),
        }

    dag_id = scenario["dag_id"]
    timeout_sec = float(scenario.get("timeout_sec", defaults.get("timeout_sec", 240)))
    clean_first = bool(scenario.get("clean_first", False))
    expect = scenario.get("expect") or {}
    reset_paths = scenario.get("reset_paths") or []
    stype = scenario.get("type", "dag_run")

    out: Dict[str, Any] = {
        "id": sid,
        "dag_id": dag_id,
        "type": stype,
        "passed": False,
        "errors": [],
        "column": None,
        "elapsed_sec": None,
        "rubric": {},
    }

    errs_pre = reset_paths_from_baseline(repo_path, reset_paths)
    out["errors"].extend(errs_pre)

    if clean_first:
        ok_del, msg = delete_feature(client, api_base, dag_id)
        if not ok_del:
            out["errors"].append(f"clean_first failed: {msg}")
            out["elapsed_sec"] = round(time.time() - t0, 3)
            out["rubric"] = apply_rubric(scenario, False, out["errors"], bundle)
            return out

    if stype == "dag_resume":
        fixture_rel = scenario.get("fixture_relative")
        if not fixture_rel:
            out["errors"].append("dag_resume requires fixture_relative")
        else:
            ok_inst, msg = install_dag_fixture(repo_path, str(fixture_rel), dag_id)
            if not ok_inst:
                out["errors"].append(f"install fixture: {msg}")
            else:
                ok_post, msg = post_dag_resume(client, api_base, dag_id)
                if not ok_post:
                    out["errors"].append(f"POST resume failed: {msg}")
    elif stype == "dag_run":
        macro_intent = scenario["macro_intent"]
        mode = scenario.get("mode", defaults.get("mode"))
        extras = scenario.get("extras")
        if extras is not None and not isinstance(extras, dict):
            extras = None
        ok_post, msg = post_dag_run(
            client,
            api_base,
            repo_path=repo_path,
            macro_intent=macro_intent,
            dag_id=dag_id,
            mode=mode,
            extras=extras,
        )
        if not ok_post:
            out["errors"].append(f"POST /api/dags/run failed: {msg}")
    else:
        out["errors"].append(f"unknown scenario type: {stype!r}")

    if out["errors"]:
        out["elapsed_sec"] = round(time.time() - t0, 3)
        out["rubric"] = apply_rubric(scenario, False, out["errors"], bundle)
        return out

    deadline = time.time() + timeout_sec
    column, poll_status = poll_feature_column(client, api_base, dag_id, repo_path, deadline)
    out["column"] = column
    out["poll_status"] = poll_status

    events = read_events_for_dag(repo_path, dag_id)
    out["event_count"] = len(events)
    elapsed = time.time() - t0

    dag_state: Optional[Dict[str, Any]] = None
    if expect.get("state_nodes"):
        for _ in range(6):
            dag_state = fetch_dag_state(client, api_base, dag_id)
            if dag_state is not None:
                break
            time.sleep(0.5)
    else:
        dag_state = fetch_dag_state(client, api_base, dag_id)

    triage = fetch_triage(client, api_base) if expect.get("triage_lists_dag_id") else []

    if poll_status == "timeout":
        out["errors"].append(f"timed out after {timeout_sec}s waiting for terminal column")
    elif poll_status == "compile_failed":
        out["errors"].append("intent compile failed (see events.jsonl)")
    else:
        out["errors"].extend(
            check_expectations(
                column,
                events,
                expect,
                elapsed_sec=elapsed,
                dag_state=dag_state,
                triage_items=triage,
                dag_id=dag_id,
            )
        )

    out["passed"] = len(out["errors"]) == 0
    out["elapsed_sec"] = round(elapsed, 3)
    out["rubric"] = apply_rubric(scenario, out["passed"], out["errors"], bundle)

    post_errs = reset_paths_from_baseline(repo_path, scenario.get("reset_paths_after") or reset_paths)
    for e in post_errs:
        out.setdefault("warnings", []).append(e)

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless Agenti-Helix eval framework (no frontend).")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("AGENTI_HELIX_API_BASE", "http://127.0.0.1:8001"),
        help="Control-plane API base URL",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path(os.environ.get("AGENTI_HELIX_REPO_ROOT", str(_default_demo_repo()))).resolve(),
        help="Target repo (default: ./demo-repo or AGENTI_HELIX_REPO_ROOT)",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=None,
        help="Path to scenarios.json (default: <repo-path>/eval/scenarios.json)",
    )
    parser.add_argument(
        "--tags",
        default=os.environ.get("AGENTI_HELIX_EVAL_TAGS", "stable"),
        help="Comma-separated tags to run, or 'all' (default: stable)",
    )
    parser.add_argument("--scenario", default=None, help="Run a single scenario id (default: all matching tags)")
    args = parser.parse_args()

    repo_path = args.repo_path.resolve()
    scen_path = args.scenarios or (repo_path / "eval" / "scenarios.json")
    if not scen_path.is_file():
        print(f"Scenarios file not found: {scen_path}", file=sys.stderr)
        return 2

    bundle = load_scenarios(scen_path)
    defaults = bundle.get("defaults") or {}
    scenarios: List[Dict[str, Any]] = list(bundle.get("scenarios") or [])

    raw_tags = args.tags.strip().lower()
    if raw_tags == "all":
        tag_filter: Set[str] = {"all"}
    else:
        tag_filter = {t.strip() for t in raw_tags.split(",") if t.strip()}

    if args.scenario:
        scenarios = [s for s in scenarios if s.get("id") == args.scenario]
        if not scenarios:
            print(f"No scenario with id={args.scenario!r}", file=sys.stderr)
            return 2
    else:
        scenarios = [s for s in scenarios if scenario_matches_tags(s, tag_filter)]

    run_order = bundle.get("run_order")
    if isinstance(run_order, list) and run_order and not args.scenario:
        order_index = {str(x): i for i, x in enumerate(run_order)}
        scenarios.sort(key=lambda s: order_index.get(str(s.get("id")), 999))

    headers = _auth_headers()
    results: List[Dict[str, Any]] = []
    timeout = httpx.Timeout(120.0, connect=5.0)
    with httpx.Client(timeout=timeout, headers=headers) as client:
        try:
            r = client.get(f"{args.api_base}/api/features", params={"limit": 1})
            if r.status_code != 200:
                print(f"API probe failed: GET /api/features -> {r.status_code}", file=sys.stderr)
                return 2
        except httpx.RequestError as exc:
            print(
                f"Cannot reach API at {args.api_base}: {exc}\n"
                "Start Judge + control plane (e.g. ./scripts/start-dev.sh).",
                file=sys.stderr,
            )
            return 2

        for sc in scenarios:
            results.append(run_one_scenario(client, args.api_base, repo_path, sc, defaults, bundle))

    non_skip = [r for r in results if not r.get("skipped")]
    passed_all = all(r.get("passed") for r in non_skip) if non_skip else True

    report = {
        "version": bundle.get("version", 1),
        "api_base": args.api_base,
        "repo_path": str(repo_path),
        "tag_filter": sorted(tag_filter),
        "dimensions": bundle.get("dimensions"),
        "results": results,
        "passed_all": passed_all,
        "counts": {
            "total": len(results),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "executed": sum(1 for r in results if not r.get("skipped")),
            "passed": sum(1 for r in results if not r.get("skipped") and r.get("passed")),
            "failed": sum(1 for r in results if not r.get("skipped") and not r.get("passed")),
        },
    }
    json_path = _report_json_path(repo_path)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md_path = _report_md_path(repo_path)
    write_report_markdown(
        md_path,
        api_base=args.api_base,
        repo_path=repo_path,
        tag_filter=tag_filter,
        bundle=bundle,
        results=results,
        passed_all=passed_all,
    )

    for r in results:
        if r.get("skipped"):
            print(f"[SKIP] {r.get('id')} — {r.get('skip_reason', '')}")
            continue
        status = "PASS" if r.get("passed") else "FAIL"
        print(f"[{status}] {r.get('id')} dag_id={r.get('dag_id')} column={r.get('column')} ({r.get('elapsed_sec')}s)")
        for err in r.get("errors") or []:
            print(f"       - {err}")
    print(f"Report JSON: {json_path}")
    print(f"Report MD:   {md_path}")

    return 0 if passed_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
