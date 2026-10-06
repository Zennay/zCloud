from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_worker_status_lines", ROOT / "scripts" / "zcloud_worker_status_lines.py"
)
status_lines = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(status_lines)


class WorkerStatusLineTests(unittest.TestCase):
    def test_projects_lane_task_status_and_latest_action(self):
        payload = {
            "chatgpt_runners": {
                "cloud": {
                    "workers": [
                        {
                            "worker_slot": 2,
                            "work_area": "control-plane",
                            "state": "live",
                            "current_task": {"title": "Central eventstream", "claim_key": "SECRET_CLAIM"},
                            "last_event": {"time": "2026-10-06T23:00:00+00:00", "event": "generation-started", "reason": "SECRET_REASON"},
                            "command": {"action": "push", "updated_at": "2026-10-06T22:59:00+00:00", "result": "SECRET_RESULT"},
                            "conversation_id": "SECRET_CONVERSATION",
                            "error": "SECRET_ERROR",
                        }
                    ]
                }
            }
        }
        result = status_lines.build_lines(payload)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(
            {
                "project_id": "cloud",
                "worker_slot": 2,
                "lane": "control-plane",
                "task": "Central eventstream",
                "status": "live",
                "last_action": "generation-started",
            },
            result["workers"][0],
        )
        encoded = json.dumps(result)
        for secret in ("SECRET_CLAIM", "SECRET_REASON", "SECRET_RESULT", "SECRET_CONVERSATION", "SECRET_ERROR"):
            self.assertNotIn(secret, encoded)

    def test_newer_command_becomes_last_action(self):
        result = status_lines.build_lines(
            {
                "chatgpt_runners": {
                    "ftmo": {
                        "workers": [
                            {
                                "worker_slot": 1,
                                "work_area": "qa-validation",
                                "state": "draining",
                                "last_event": {"time": "2026-10-06T22:00:00+00:00", "event": "heartbeat"},
                                "command": {"action": "drain", "updated_at": "2026-10-06T22:01:00+00:00"},
                            }
                        ]
                    }
                }
            }
        )
        self.assertEqual("command:drain", result["workers"][0]["last_action"])

    def test_execution_lane_fallback_is_supported(self):
        result = status_lines.build_lines(
            {
                "chatgpt_runners": {
                    "supa": {
                        "workers": [
                            {
                                "worker_slot": 1,
                                "execution_lane": {"lane_id": "quality-validation"},
                                "state": "starting",
                            }
                        ]
                    }
                }
            }
        )
        self.assertEqual("quality-validation", result["workers"][0]["lane"])

    def test_missing_lane_is_fail_visible(self):
        result = status_lines.build_lines(
            {"chatgpt_runners": {"cloud": {"workers": [{"worker_slot": 1, "state": "live"}]}}}
        )
        self.assertFalse(result["coverage_complete"])
        self.assertIsNone(result["workers"][0]["lane"])

    def test_unknown_state_is_fail_visible(self):
        result = status_lines.build_lines(
            {"chatgpt_runners": {"cloud": {"workers": [{"worker_slot": 1, "work_area": "control-plane", "state": "mystery"}]}}}
        )
        self.assertFalse(result["coverage_complete"])
        self.assertEqual("offline", result["workers"][0]["status"])
        self.assertEqual(1, result["malformed_workers"])

    def test_invalid_worker_slot_is_omitted_fail_visible(self):
        result = status_lines.build_lines(
            {"chatgpt_runners": {"cloud": {"workers": [{"worker_slot": 0, "state": "live"}]}}}
        )
        self.assertFalse(result["coverage_complete"])
        self.assertEqual([], result["workers"])

    def test_task_text_is_bounded_and_single_line(self):
        result = status_lines.build_lines(
            {
                "chatgpt_runners": {
                    "cloud": {
                        "workers": [
                            {
                                "worker_slot": 1,
                                "state": "paused",
                                "current_task": {"title": "x\n" + "y" * 300},
                            }
                        ]
                    }
                }
            }
        )
        task = result["workers"][0]["task"]
        self.assertNotIn("\n", task)
        self.assertLessEqual(len(task), 160)

    def test_symlink_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "runner.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(status_lines.StatusLineError):
                status_lines._load(link)


if __name__ == "__main__":
    unittest.main()
