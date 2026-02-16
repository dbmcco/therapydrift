import unittest

from therapydrift.drift import compute_therapy_drift
from therapydrift.specs import TherapydriftSpec


class TestTherapyDrift(unittest.TestCase):
    def test_green_without_signals(self) -> None:
        spec = TherapydriftSpec.from_raw({"schema": 1, "min_signal_count": 2})
        task = {"id": "t1", "title": "Task", "status": "in-progress", "log": []}
        report = compute_therapy_drift(task_id="t1", task_title="Task", spec=spec, task=task, tasks={"t1": task})
        self.assertEqual("green", report["score"])
        self.assertEqual([], report["findings"])

    def test_flags_repeated_signals_and_open_followups(self) -> None:
        spec = TherapydriftSpec.from_raw({"schema": 1, "min_signal_count": 2})
        task = {
            "id": "t1",
            "title": "Task",
            "status": "in-progress",
            "log": [
                {"timestamp": "2026-02-16T10:00:00+00:00", "message": "Coredrift: yellow (scope_drift)"},
                {"timestamp": "2026-02-16T10:05:00+00:00", "message": "Specdrift: yellow (spec_not_updated)"},
            ],
        }
        follow = {"id": "drift-scope-t1", "status": "open", "blocked_by": ["t1"]}
        report = compute_therapy_drift(
            task_id="t1",
            task_title="Task",
            spec=spec,
            task=task,
            tasks={"t1": task, "drift-scope-t1": follow},
        )
        kinds = {f["kind"] for f in report["findings"]}
        self.assertIn("repeated_drift_signals", kinds)
        self.assertIn("unresolved_drift_followups", kinds)
        self.assertIn("missing_recovery_plan", kinds)
        self.assertEqual("yellow", report["score"])
        self.assertEqual(2, report["telemetry"]["new_signal_count"])

    def test_ignores_self_signals(self) -> None:
        spec = TherapydriftSpec.from_raw({"schema": 1, "min_signal_count": 1})
        task = {
            "id": "t1",
            "title": "Task",
            "status": "in-progress",
            "log": [
                {"timestamp": "2026-02-16T10:00:00+00:00", "message": "Therapydrift: yellow (repeated_drift_signals)"},
                {"timestamp": "2026-02-16T10:01:00+00:00", "message": "Speedrift: yellow (scope_drift)"},
            ],
        }
        report = compute_therapy_drift(task_id="t1", task_title="Task", spec=spec, task=task, tasks={"t1": task})
        self.assertEqual(1, report["telemetry"]["drift_signal_count"])
        self.assertEqual(1, report["telemetry"]["ignored_self_signals"])


if __name__ == "__main__":
    unittest.main()
