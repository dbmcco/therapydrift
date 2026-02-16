import unittest
from datetime import datetime, timezone

from therapydrift.cli import _evaluate_auto_action_policy
from therapydrift.specs import TherapydriftSpec


class TestLoopSafety(unittest.TestCase):
    def test_blocks_without_new_evidence(self) -> None:
        spec = TherapydriftSpec.from_raw(
            {
                "schema": 1,
                "min_new_signals": 1,
                "cooldown_seconds": 0,
                "max_auto_actions_per_hour": 2,
            }
        )
        now = datetime(2026, 2, 16, 11, 0, tzinfo=timezone.utc)
        findings = [{"kind": "repeated_drift_signals"}]
        telemetry = {"new_signal_count": 0, "open_followup_ids": ["drift-scope-t1"]}
        task_state = {"open_followup_ids": ["drift-scope-t1"], "auto_action_timestamps": []}

        policy = _evaluate_auto_action_policy(
            spec=spec,
            findings=findings,
            telemetry=telemetry,
            task_state=task_state,
            now=now,
        )
        self.assertFalse(policy["allow_auto_action"])
        self.assertEqual("no_new_evidence", policy["reason"])

    def test_blocks_on_cooldown(self) -> None:
        spec = TherapydriftSpec.from_raw(
            {
                "schema": 1,
                "cooldown_seconds": 1800,
                "max_auto_actions_per_hour": 2,
                "min_new_signals": 1,
            }
        )
        now = datetime(2026, 2, 16, 11, 0, tzinfo=timezone.utc)
        findings = [{"kind": "missing_recovery_plan"}]
        telemetry = {"new_signal_count": 2, "open_followup_ids": ["drift-scope-t1"]}
        task_state = {
            "open_followup_ids": ["drift-scope-t0"],
            "auto_action_timestamps": ["2026-02-16T10:45:00+00:00"],
            "auto_action_total": 1,
        }

        policy = _evaluate_auto_action_policy(
            spec=spec,
            findings=findings,
            telemetry=telemetry,
            task_state=task_state,
            now=now,
        )
        self.assertFalse(policy["allow_auto_action"])
        self.assertEqual("cooldown_active", policy["reason"])

    def test_opens_circuit_breaker(self) -> None:
        spec = TherapydriftSpec.from_raw(
            {
                "schema": 1,
                "circuit_breaker_after": 2,
                "cooldown_seconds": 0,
                "max_auto_actions_per_hour": 5,
            }
        )
        now = datetime(2026, 2, 16, 11, 0, tzinfo=timezone.utc)
        findings = [{"kind": "missing_recovery_plan"}]
        telemetry = {"new_signal_count": 3, "open_followup_ids": ["drift-scope-t1"]}
        task_state = {"auto_action_total": 2, "auto_action_timestamps": []}

        policy = _evaluate_auto_action_policy(
            spec=spec,
            findings=findings,
            telemetry=telemetry,
            task_state=task_state,
            now=now,
        )
        self.assertFalse(policy["allow_auto_action"])
        self.assertEqual("circuit_breaker_open", policy["reason"])


if __name__ == "__main__":
    unittest.main()
