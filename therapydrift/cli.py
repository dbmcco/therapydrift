from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from therapydrift.contracts import format_default_contract_block
from therapydrift.drift import compute_therapy_drift
from therapydrift.specs import TherapydriftSpec, extract_therapydrift_spec, parse_therapydrift_spec
from therapydrift.workgraph import Workgraph, find_workgraph_dir, load_tasks


class ExitCode:
    ok = 0
    findings = 3
    usage = 2


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _state_path(wg_dir: Path) -> Path:
    return wg_dir / ".therapydrift" / "state.json"


def _load_automation_state(wg_dir: Path) -> dict:
    p = _state_path(wg_dir)
    if not p.exists():
        return {"tasks": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"tasks": {}}
        tasks = data.get("tasks")
        if not isinstance(tasks, dict):
            data["tasks"] = {}
        return data
    except Exception:
        return {"tasks": {}}


def _save_automation_state(wg_dir: Path, state: dict) -> None:
    p = _state_path(wg_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _evaluate_auto_action_policy(
    *,
    spec: TherapydriftSpec,
    findings: list[dict],
    telemetry: dict,
    task_state: dict,
    now: datetime,
) -> dict:
    kinds = {str(f.get("kind") or "") for f in findings}
    actionable_kinds = {"repeated_drift_signals", "unresolved_drift_followups", "missing_recovery_plan"}
    has_actionable_findings = bool(kinds & actionable_kinds)

    raw_actions = [str(x) for x in (task_state.get("auto_action_timestamps") or [])]
    action_dts = [dt for dt in (_parse_ts(x) for x in raw_actions) if dt is not None]
    one_hour_ago = now - timedelta(hours=1)
    recent_actions = [dt for dt in action_dts if dt >= one_hour_ago]
    last_action_dt = max(action_dts) if action_dts else None

    total_actions = int(task_state.get("auto_action_total", 0) or 0)
    circuit_breaker_open = total_actions >= int(spec.circuit_breaker_after)
    cooldown_active = False
    if last_action_dt is not None and int(spec.cooldown_seconds) > 0:
        cooldown_active = (now - last_action_dt) < timedelta(seconds=int(spec.cooldown_seconds))

    prev_followups = set(str(x) for x in (task_state.get("open_followup_ids") or []))
    cur_followups = set(str(x) for x in (telemetry.get("open_followup_ids") or []))
    open_followups_changed = cur_followups != prev_followups
    new_signal_count = int(telemetry.get("new_signal_count", 0) or 0)
    has_new_evidence = new_signal_count >= int(spec.min_new_signals) or open_followups_changed

    allow = False
    reason = "no_actionable_findings"
    if has_actionable_findings:
        if circuit_breaker_open:
            reason = "circuit_breaker_open"
        elif int(spec.max_auto_actions_per_hour) == 0:
            reason = "hourly_budget_disabled"
        elif len(recent_actions) >= int(spec.max_auto_actions_per_hour):
            reason = "hourly_budget_exhausted"
        elif cooldown_active:
            reason = "cooldown_active"
        elif not has_new_evidence:
            reason = "no_new_evidence"
        else:
            allow = True
            reason = "allowed"

    return {
        "allow_auto_action": allow,
        "reason": reason,
        "has_actionable_findings": has_actionable_findings,
        "new_signal_count": new_signal_count,
        "open_followups_changed": open_followups_changed,
        "recent_action_count_1h": len(recent_actions),
        "cooldown_active": cooldown_active,
        "circuit_breaker_open": circuit_breaker_open,
    }


def _update_automation_state(
    *,
    state: dict,
    task_id: str,
    telemetry: dict,
    policy: dict,
    action_created: bool,
    now: datetime,
) -> None:
    tasks = state.setdefault("tasks", {})
    cur = dict(tasks.get(task_id) or {})

    cur["last_check_ts"] = now.isoformat()
    latest_signal_ts = telemetry.get("latest_signal_ts")
    if latest_signal_ts:
        cur["latest_signal_ts"] = str(latest_signal_ts)
    cur["drift_signal_count"] = int(telemetry.get("drift_signal_count", 0) or 0)
    cur["open_followup_ids"] = [str(x) for x in (telemetry.get("open_followup_ids") or [])]

    raw_actions = [str(x) for x in (cur.get("auto_action_timestamps") or [])]
    day_ago = now - timedelta(hours=24)
    kept: list[str] = []
    for ts in raw_actions:
        dt = _parse_ts(ts)
        if dt is not None and dt >= day_ago:
            kept.append(ts)
    if action_created:
        kept.append(now.isoformat())
        cur["auto_action_total"] = int(cur.get("auto_action_total", 0) or 0) + 1
    else:
        cur["auto_action_total"] = int(cur.get("auto_action_total", 0) or 0)
    cur["auto_action_timestamps"] = kept
    cur["circuit_breaker_open"] = bool(policy.get("circuit_breaker_open"))

    tasks[task_id] = cur


def _emit_text(report: dict) -> None:
    task_id = report.get("task_id")
    title = report.get("task_title")
    score = report.get("score")
    findings = report.get("findings") or []

    print(f"{task_id}: {title}")
    print(f"score: {score}")
    if not findings:
        print("findings: none")
        return

    print("findings:")
    for f in findings:
        print(f"- [{f.get('severity')}] {f.get('kind')}: {f.get('summary')}")


def _write_state(*, wg_dir: Path, report: dict) -> None:
    try:
        out_dir = wg_dir / ".therapydrift"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "last.json").write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def _maybe_write_log(wg: Workgraph, task_id: str, report: dict) -> None:
    findings = report.get("findings") or []
    score = report.get("score", "unknown")
    recs = report.get("recommendations") or []

    if not findings:
        msg = "Therapydrift: OK (no findings)"
    else:
        kinds = ", ".join(sorted({str(f.get("kind")) for f in findings}))
        msg = f"Therapydrift: {score} ({kinds})"
        if recs:
            next_action = str(recs[0].get("action") or "").strip()
            if next_action:
                msg += f" | next: {next_action}"

    wg.wg_log(task_id, msg)


def _maybe_create_followups(wg: Workgraph, report: dict, *, policy: dict) -> bool:
    if not bool(policy.get("allow_auto_action")):
        return False

    task_id = str(report["task_id"])
    task_title = str(report.get("task_title") or task_id)
    findings = report.get("findings") or []
    recs = report.get("recommendations") or []
    if not findings:
        return False

    follow_id = f"drift-therapy-{task_id}"
    title = f"therapy: {task_title}"
    action_lines = "\n".join([f"- {str(r.get('action') or '').strip()}" for r in recs if str(r.get("action") or "").strip()])
    if not action_lines:
        action_lines = "- Re-synchronize intent, scope, and open drift follow-up tasks."

    desc = (
        "Run a self-healing cycle for persistent drift signals.\n\n"
        "Context:\n"
        f"- Origin task: {task_id}\n"
        f"- Findings: {', '.join(sorted({str(f.get('kind')) for f in findings}))}\n\n"
        "Recommended actions:\n"
        f"{action_lines}\n\n"
        + format_default_contract_block(mode="explore", objective=title, touch=[])
        + "\n"
        + (report.get("_therapydrift_block") or "").strip()
        + "\n"
    )

    wg.ensure_task(
        task_id=follow_id,
        title=title,
        description=desc,
        blocked_by=[task_id],
        tags=["drift", "therapy"],
    )
    return True


def _load_task(*, wg: Workgraph, task_id: str) -> dict:
    task = wg.show_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")
    return task


def cmd_wg_check(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    wg = Workgraph(wg_dir=wg_dir, project_dir=project_dir)

    task_id = str(args.task)
    task = _load_task(wg=wg, task_id=task_id)
    title = str(task.get("title") or task_id)
    description = str(task.get("description") or "")

    raw_block = extract_therapydrift_spec(description)
    if raw_block is None:
        report = {
            "task_id": task_id,
            "task_title": title,
            "score": "green",
            "spec": None,
            "telemetry": {"note": "no therapydrift block"},
            "findings": [],
            "recommendations": [],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=False))
        else:
            _emit_text(report)
        return ExitCode.ok

    try:
        spec_raw = parse_therapydrift_spec(raw_block)
        spec = TherapydriftSpec.from_raw(spec_raw)
    except Exception as e:
        report = {
            "task_id": task_id,
            "task_title": title,
            "score": "yellow",
            "spec": None,
            "telemetry": {"parse_error": str(e)},
            "findings": [
                {
                    "kind": "invalid_therapydrift_spec",
                    "severity": "warn",
                    "summary": "therapydrift block present but could not be parsed",
                }
            ],
            "recommendations": [
                {
                    "priority": "high",
                    "action": "Fix the therapydrift TOML block so it parses",
                    "rationale": "Therapydrift can only guide self-healing when it can read the configuration.",
                }
            ],
        }
        report["_therapydrift_block"] = f"```therapydrift\n{raw_block}\n```"
        _write_state(wg_dir=wg_dir, report=report)
        if args.write_log:
            _maybe_write_log(wg, task_id, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=False))
        else:
            _emit_text(report)
        return ExitCode.findings

    tasks = load_tasks(wg_dir)
    state = _load_automation_state(wg_dir)
    task_state = dict((state.get("tasks") or {}).get(task_id) or {})
    previous_latest_signal_ts = str(task_state.get("latest_signal_ts") or "") or None
    report = compute_therapy_drift(
        task_id=task_id,
        task_title=title,
        spec=spec,
        task=task,
        tasks=tasks,
        previous_latest_signal_ts=previous_latest_signal_ts,
    )
    report["_therapydrift_block"] = f"```therapydrift\n{raw_block}\n```"

    now = datetime.now(timezone.utc)
    policy = _evaluate_auto_action_policy(
        spec=spec,
        findings=list(report.get("findings") or []),
        telemetry=dict(report.get("telemetry") or {}),
        task_state=task_state,
        now=now,
    )
    telemetry = dict(report.get("telemetry") or {})
    telemetry["auto_action_policy"] = policy
    report["telemetry"] = telemetry

    _write_state(wg_dir=wg_dir, report=report)

    if args.write_log:
        _maybe_write_log(wg, task_id, report)
    action_created = False
    if args.create_followups:
        action_created = _maybe_create_followups(wg, report, policy=policy)
    _update_automation_state(
        state=state,
        task_id=task_id,
        telemetry=telemetry,
        policy=policy,
        action_created=action_created,
        now=now,
    )
    _save_automation_state(wg_dir, state)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        _emit_text(report)

    return ExitCode.findings if report.get("findings") else ExitCode.ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="therapydrift")
    p.add_argument("--dir", help="Project directory (or .workgraph dir). Defaults to cwd search.")
    p.add_argument("--json", action="store_true", help="JSON output (where supported)")

    sub = p.add_subparsers(dest="cmd", required=True)

    wg = sub.add_parser("wg", help="Workgraph-integrated commands")
    wg_sub = wg.add_subparsers(dest="wg_cmd", required=True)

    check = wg_sub.add_parser("check", help="Check self-healing drift readiness (requires a therapydrift block in the task)")
    check.add_argument("--task", help="Task id to check")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_wg_check)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
