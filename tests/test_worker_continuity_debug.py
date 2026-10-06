import importlib.util
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_worker_continuity_debug.py"
SPEC = importlib.util.spec_from_file_location("continuity", MODULE_PATH)
continuity = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(continuity)


class ContinuityDebugTests(unittest.TestCase):
    def test_extract_runtime_reads_real_runner_live_shape(self):
        payload = {
            "chatgpt_runners": {
                "cloud": {
                    "workers": [
                        {
                            "worker_id": "cloud::w1",
                            "state": "live",
                            "generating": False,
                            "sending": False,
                            "progress_age_seconds": 12,
                            "age_seconds": 3,
                        },
                        {
                            "worker_id": "cloud::w2",
                            "state": "live",
                            "generating": True,
                            "sending": False,
                            "progress_age_seconds": 700,
                            "age_seconds": 4,
                        },
                    ]
                }
            }
        }
        live, busy, ghost, details = continuity.extract_runtime(payload)
        self.assertEqual({"cloud::w1", "cloud::w2"}, live)
        self.assertEqual({"cloud::w2"}, busy)
        self.assertEqual({"cloud::w2"}, ghost)
        self.assertEqual(700, details["cloud::w2"]["progress_age_seconds"])

    def test_stale_runner_is_not_counted_live(self):
        payload = {
            "chatgpt_runners": {
                "supa": {
                    "workers": [{
                        "worker_id": "supa::w1",
                        "state": "stale",
                        "generating": False,
                        "sending": False,
                    }]
                }
            }
        }
        live, busy, ghost, _ = continuity.extract_runtime(payload)
        self.assertEqual(set(), live)
        self.assertEqual(set(), busy)
        self.assertEqual(set(), ghost)

    def test_classify_reports_allocator_runtime_and_ghost_failures(self):
        snapshot = {
            "desired_total": 8,
            "allocated_count": 6,
            "live_count": 5,
            "api_ok": True,
            "memory": {"available_pct": 20},
            "load": {"load1_per_core": 0.5},
            "firefox": {"count": 1},
            "ghost_generating_keys": ["cloud::w1"],
        }
        self.assertEqual(
            ["allocator-capacity-loss", "browser-runtime-gap", "ghost-generating"],
            continuity.classify(snapshot, 8),
        )

    def test_firefox_restart_requires_two_missing_samples(self):
        snapshot = {
            "allocated_count": 1,
            "desired_total": 1,
            "allocated_keys": ["cloud::w1"],
            "missing_live_keys": [],
            "busy_keys": [],
            "ghost_generating_keys": [],
            "live_count": 1,
            "memory": {"available_pct": 50},
            "load": {"load1_per_core": 0.1},
            "firefox": {"count": 0},
            "dynamic": {"body": {"dynamic_workers": {"chatgpt_count": 1, "claude_count": 0}}},
        }
        with mock.patch.object(continuity, "runner_action", return_value={"ok": True}) as action:
            streaks = {}
            worker_streaks = {}
            last_actions = {}
            first = continuity.maybe_remediate(
                snapshot,
                base_url="http://127.0.0.1:8765",
                streaks=streaks,
                worker_streaks=worker_streaks,
                last_actions=last_actions,
            )
            second = continuity.maybe_remediate(
                snapshot,
                base_url="http://127.0.0.1:8765",
                streaks=streaks,
                worker_streaks=worker_streaks,
                last_actions=last_actions,
            )
        self.assertEqual([], first)
        self.assertEqual(1, len(second))
        action.assert_called_once_with("http://127.0.0.1:8765", "", "restart_firefox")

    def test_ghost_generation_requests_fresh_chat_without_global_restart(self):
        snapshot = {
            "allocated_count": 1,
            "desired_total": 1,
            "allocated_keys": ["cloud::w1"],
            "missing_live_keys": [],
            "busy_keys": ["cloud::w1"],
            "ghost_generating_keys": ["cloud::w1"],
            "live_count": 1,
            "memory": {"available_pct": 50},
            "load": {"load1_per_core": 0.1},
            "firefox": {"count": 1},
            "dynamic": {"body": {"dynamic_workers": {"chatgpt_count": 1, "claude_count": 0}}},
        }
        calls = []
        def fake_action(base, project, action):
            calls.append((project, action))
            return {"ok": True, "project_id": project, "action": action}

        with mock.patch.object(continuity, "runner_action", side_effect=fake_action):
            streaks = {}
            worker_streaks = {}
            last_actions = {}
            continuity.maybe_remediate(
                snapshot,
                base_url="http://127.0.0.1:8765",
                streaks=streaks,
                worker_streaks=worker_streaks,
                last_actions=last_actions,
            )
            second = continuity.maybe_remediate(
                snapshot,
                base_url="http://127.0.0.1:8765",
                streaks=streaks,
                worker_streaks=worker_streaks,
                last_actions=last_actions,
            )

        self.assertEqual([("cloud::w1", "new_chat")], calls)
        self.assertEqual("new_chat", second[0]["action"])


if __name__ == "__main__":
    unittest.main()
