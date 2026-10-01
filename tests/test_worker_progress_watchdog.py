import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_worker_watchdog.py"
SPEC = importlib.util.spec_from_file_location("zcloud_worker_watchdog", MODULE_PATH)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(watchdog)


class WorkerWatchdogDecisionTests(unittest.TestCase):
    def decision(self, **overrides):
        values = {
            "stalled_seconds": 0,
            "activity_age_seconds": 999999,
            "runtime_state": "live",
            "generating": False,
            "progress_age_seconds": None,
            "seconds_since_last_attempt": 999999,
            "seconds_since_last_new_chat": 999999,
            "human_gate": False,
        }
        values.update(overrides)
        return watchdog.choose_action(**values)

    def test_does_not_touch_human_gate(self):
        self.assertIsNone(self.decision(
            stalled_seconds=99999,
            runtime_state="offline",
            human_gate=True,
        ))

    def test_pushes_when_material_progress_is_stale_and_worker_is_quiet(self):
        self.assertEqual(
            "push",
            self.decision(stalled_seconds=watchdog.PUSH_AFTER_SECONDS),
        )

    def test_restarts_worker_after_longer_material_stall(self):
        self.assertEqual(
            "new_chat",
            self.decision(stalled_seconds=watchdog.RESTART_AFTER_SECONDS),
        )

    def test_offline_worker_is_restarted_earlier(self):
        self.assertEqual(
            "new_chat",
            self.decision(
                stalled_seconds=watchdog.OFFLINE_RESTART_AFTER_SECONDS,
                runtime_state="offline",
            ),
        )

    def test_recent_generation_activity_prevents_interrupt(self):
        self.assertIsNone(self.decision(
            stalled_seconds=watchdog.RESTART_AFTER_SECONDS + 600,
            activity_age_seconds=30,
        ))

    def test_active_generation_is_protected_while_progress_is_recent(self):
        self.assertIsNone(self.decision(
            stalled_seconds=watchdog.RESTART_AFTER_SECONDS + 600,
            generating=True,
            progress_age_seconds=60,
        ))

    def test_material_fingerprint_changes_when_commit_changes(self):
        first = {
            "queue_id": "q1",
            "project_id": "zssh",
            "worker_slot": 1,
            "queue": {"status": "in_progress", "evidence": "", "blocker": ""},
            "sample": {"hash": "aaa", "commits": 10, "progress": 40, "message": "x"},
            "queue_result_event": None,
            "human_gate": False,
        }
        second = dict(first)
        second["sample"] = dict(first["sample"], hash="bbb", commits=11)
        self.assertNotEqual(
            watchdog.material_fingerprint(first),
            watchdog.material_fingerprint(second),
        )


if __name__ == "__main__":
    unittest.main()
