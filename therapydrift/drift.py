from __future__ import annotations

from datetime import datetime
from dataclasses import asdict, dataclass
from typing import Any

from therapydrift.specs import TherapydriftSpec


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    summary: str
    details: dict[str, Any] | None = None


_DRIFT_PREFIXES = (
    "Coredrift:",
    "Speedrift:",
    "Specdrift:",
    "Datadrift:",
    "Depsdrift:",
    "Uxdrift:",
    "Therapydrift:",
)


def _task_status(task: dict[str, Any]) -> str:
    return str(task.get("status") or "")


def _blocked_by(task: dict[str, Any]) -> list[str]:
    return [str(x) for x in (task.get("blocked_by") or [])]


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def compute_therapy_drift(
    *,
    task_id: str,
    task_title: str,
    spec: TherapydriftSpec,
    task: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    previous_latest_signal_ts: str | None = None,
) -> dict[str, Any]:
    findings: list[Finding] = []

    logs = task.get("log") or []
    drift_signals: list[dict[str, str | None]] = []
    ignored_self_signals = 0
    for e in logs:
        if not isinstance(e, dict):
            continue
        message = str(e.get("message") or "")
        if not message.startswith(_DRIFT_PREFIXES):
            continue
        if any(message.startswith(p) for p in spec.ignore_signal_prefixes):
            ignored_self_signals += 1
            continue
        drift_signals.append(
            {
                "message": message,
                "timestamp": str(e.get("timestamp") or "") or None,
            }
        )

    open_followups: list[str] = []
    for t in tasks.values():
        tid = str(t.get("id") or "")
        if not tid or tid == task_id:
            continue
        if _task_status(t) not in {"open", "in-progress"}:
            continue
        if task_id not in _blocked_by(t):
            continue
        if any(tid.startswith(prefix) for prefix in spec.followup_prefixes):
            open_followups.append(tid)
    open_followups = sorted(set(open_followups))

    latest_signal_dt: datetime | None = None
    for s in drift_signals:
        parsed = _parse_ts(s.get("timestamp"))
        if parsed is None:
            continue
        if latest_signal_dt is None or parsed > latest_signal_dt:
            latest_signal_dt = parsed

    previous_latest_dt = _parse_ts(previous_latest_signal_ts)
    new_signal_count = 0
    if previous_latest_dt is None:
        new_signal_count = len(drift_signals)
    else:
        for s in drift_signals:
            parsed = _parse_ts(s.get("timestamp"))
            if parsed is not None and parsed > previous_latest_dt:
                new_signal_count += 1

    telemetry: dict[str, Any] = {
        "drift_signal_count": len(drift_signals),
        "new_signal_count": new_signal_count,
        "ignored_self_signals": ignored_self_signals,
        "open_drift_followups": len(open_followups),
        "open_followup_ids": open_followups[:50],
        "latest_signal_ts": latest_signal_dt.isoformat() if latest_signal_dt else None,
    }

    if spec.schema != 1:
        findings.append(
            Finding(
                kind="unsupported_schema",
                severity="warn",
                summary=f"Unsupported therapydrift schema: {spec.schema} (expected 1)",
            )
        )

    if len(drift_signals) >= spec.min_signal_count:
        findings.append(
            Finding(
                kind="repeated_drift_signals",
                severity="warn",
                summary=(
                    f"Task has repeated drift signals ({len(drift_signals)} >= {spec.min_signal_count})"
                ),
                details={"recent_signals": drift_signals[-5:]},
            )
        )

    if open_followups:
        findings.append(
            Finding(
                kind="unresolved_drift_followups",
                severity="warn",
                summary=f"Task has unresolved drift follow-up tasks ({len(open_followups)})",
                details={"tasks": open_followups[:20]},
            )
        )

    therapy_task_id = f"drift-therapy-{task_id}"
    therapy_exists = therapy_task_id in tasks and _task_status(tasks[therapy_task_id]) in {
        "open",
        "in-progress",
        "done",
    }
    telemetry["therapy_task_exists"] = bool(therapy_exists)

    if spec.require_recovery_plan and findings and not therapy_exists:
        findings.append(
            Finding(
                kind="missing_recovery_plan",
                severity="warn",
                summary="No therapy recovery task exists for this drifting task",
                details={"expected_task_id": therapy_task_id},
            )
        )

    score = "green"
    if any(f.severity == "warn" for f in findings):
        score = "yellow"
    if any(f.severity == "error" for f in findings):
        score = "red"

    recommendations: list[dict[str, Any]] = []
    for f in findings:
        if f.kind == "repeated_drift_signals":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Run a self-healing cycle: tighten touch scope and split hardening work",
                    "rationale": "Repeated drift signals indicate intent is not staying synchronized with execution.",
                }
            )
        elif f.kind == "unresolved_drift_followups":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Resolve or re-scope open drift follow-up tasks before adding new scope",
                    "rationale": "Stacking unresolved follow-ups compounds execution drift over time.",
                }
            )
        elif f.kind == "missing_recovery_plan":
            recommendations.append(
                {
                    "priority": "high",
                    "action": f"Create and complete {therapy_task_id} to consolidate remediation",
                    "rationale": "A dedicated recovery lane prevents drift fixes from bloating the current task.",
                }
            )
        elif f.kind == "unsupported_schema":
            recommendations.append(
                {
                    "priority": "high",
                    "action": "Set therapydrift schema = 1",
                    "rationale": "Only schema v1 is currently supported.",
                }
            )

    seen_actions: set[str] = set()
    recommendations = [r for r in recommendations if not (r["action"] in seen_actions or seen_actions.add(r["action"]))]  # type: ignore[arg-type]

    return {
        "task_id": task_id,
        "task_title": task_title,
        "score": score,
        "spec": asdict(spec),
        "telemetry": telemetry,
        "findings": [asdict(f) for f in findings],
        "recommendations": recommendations,
    }
