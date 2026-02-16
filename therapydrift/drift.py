from __future__ import annotations

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


def compute_therapy_drift(
    *,
    task_id: str,
    task_title: str,
    spec: TherapydriftSpec,
    task: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    findings: list[Finding] = []

    logs = task.get("log") or []
    messages: list[str] = []
    for e in logs:
        if isinstance(e, dict):
            messages.append(str(e.get("message") or ""))

    drift_signals = [m for m in messages if m.startswith(_DRIFT_PREFIXES)]

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

    telemetry: dict[str, Any] = {
        "drift_signal_count": len(drift_signals),
        "open_drift_followups": len(open_followups),
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
