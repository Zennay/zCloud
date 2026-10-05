import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
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
            "prompt_sent_age_seconds": 0,
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

    def test_refreshes_chat_when_last_real_prompt_is_too_old(self):
        self.assertEqual(
            "new_chat",
            self.decision(
                stalled_seconds=watchdog.PUSH_AFTER_SECONDS,
                activity_age_seconds=10,
                prompt_sent_age_seconds=watchdog.PROMPT_STALE_REFRESH_SECONDS,
            ),
        )

    def test_recent_prompt_prevents_prompt_stale_refresh(self):
        self.assertIsNone(self.decision(
            stalled_seconds=watchdog.PUSH_AFTER_SECONDS,
            activity_age_seconds=10,
            prompt_sent_age_seconds=30,
        ))

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


class WorkerWatchdogStaleCommandTests(unittest.TestCase):
    def test_global_cleanup_includes_old_project_level_commands_only(self):
        now = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "history.db"
            conn = sqlite3.connect(db)
            conn.execute("""CREATE TABLE runner_commands(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT,
                action TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                result TEXT
            )""")
            old = (now - timedelta(seconds=601)).isoformat()
            recent = (now - timedelta(seconds=30)).isoformat()
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("raiseai", "start", "pending", old, old),
            )
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("cloud::w1", "push", "pending", recent, recent),
            )
            conn.commit()
            conn.close()

            cleared = watchdog.clear_stale_pending_commands(
                db,
                project_id=None,
                now=now,
                min_age_seconds=300,
            )
            self.assertEqual([1], cleared)

            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT id,status,result FROM runner_commands ORDER BY id"
            ).fetchall()
            conn.close()
            self.assertEqual("failed", rows[0][1])
            self.assertIn("superseded stale pending command", rows[0][2])
            self.assertEqual("pending", rows[1][1])


class WorkerWatchdogCriticalMemoryRecoveryTests(unittest.TestCase):
    def test_critical_memory_with_all_workers_offline_requires_firefox_recovery(self):
        runtime = {
            "chatgpt_runners": {
                "cloud": {
                    "workers": [{
                        "worker_id": "cloud::w1",
                        "state": "offline",
                        "generating": False,
                        "sending": False,
                    }]
                }
            }
        }
        self.assertTrue(watchdog.critical_memory_firefox_recovery_needed(
            selected_keys={"cloud::w1"},
            runtime=runtime,
            memory_guard={"pressure": "critical"},
        ))

    def test_critical_memory_does_not_restart_during_generation(self):
        runtime = {
            "chatgpt_runners": {
                "cloud": {
                    "workers": [{
                        "worker_id": "cloud::w1",
                        "state": "live",
                        "generating": True,
                        "sending": False,
                    }]
                }
            }
        }
        self.assertFalse(watchdog.critical_memory_firefox_recovery_needed(
            selected_keys={"cloud::w1"},
            runtime=runtime,
            memory_guard={"pressure": "critical"},
        ))

    def test_guarded_memory_does_not_force_firefox_recovery(self):
        runtime = {
            "chatgpt_runners": {
                "cloud": {
                    "workers": [{
                        "worker_id": "cloud::w1",
                        "state": "offline",
                        "generating": False,
                        "sending": False,
                    }]
                }
            }
        }
        self.assertFalse(watchdog.critical_memory_firefox_recovery_needed(
            selected_keys={"cloud::w1"},
            runtime=runtime,
            memory_guard={"pressure": "guarded"},
        ))


class WorkerWatchdogRuntimeRecoveryTests(unittest.TestCase):
    def test_critical_memory_lost_runtime_restarts_firefox(self):
        original_api_call = watchdog.api_call
        calls = []

        def fake_api_call(base_url, method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET" and path == "/api/runner-targets":
                return 200, {
                    "global_allocation": {
                        "workers": [{
                            "worker_key": "cloud::w1",
                            "project_id": "cloud",
                            "worker_slot": 1,
                            "queue_id": "q1",
                        }]
                    }
                }
            if method == "GET" and path == "/api/status":
                return 200, {
                    "chatgpt_firefox": {
                        "active": True,
                        "state": "active",
                        "runtime_mode": "standalone",
                    },
                    "chatgpt_runners": {
                        "cloud": {
                            "workers": [{
                                "worker_id": "cloud::w1",
                                "state": "offline",
                                "generating": False,
                                "sending": False,
                            }]
                        }
                    },
                    "dynamic_workers": {
                        "memory_guard": {
                            "pressure": "critical",
                            "available_mb": 800,
                            "swap_healthy": False,
                        }
                    },
                }
            if method == "POST" and path == "/api/runner-control":
                return 200, {"ok": True, "status": {"active": True}}
            raise AssertionError((method, path, payload))

        watchdog.api_call = fake_api_call
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = watchdog.run_once(
                    db_path=Path(tmp) / "history.db",
                    state_path=Path(tmp) / "state.json",
                    base_url="http://127.0.0.1:8765",
                )
        finally:
            watchdog.api_call = original_api_call

        self.assertTrue(result["ok"], result)
        self.assertEqual("critical-memory-worker-runtime-lost", result["reason"])
        self.assertEqual("restart_firefox", result["global_action"]["action"])
        self.assertTrue(result["global_action"]["ok"])
        self.assertTrue(any(
            method == "POST"
            and path == "/api/runner-control"
            and payload == {"project_id": "", "action": "restart_firefox"}
            for method, path, payload in calls
        ))

    def test_inactive_firefox_is_recovered_before_stall_timers(self):
        original_api_call = watchdog.api_call
        calls = []

        def fake_api_call(base_url, method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET" and path == "/api/runner-targets":
                return 200, {
                    "global_allocation": {
                        "workers": [{
                            "worker_key": "cloud::w1",
                            "project_id": "cloud",
                            "worker_slot": 1,
                            "queue_id": "q1",
                        }]
                    }
                }
            if method == "GET" and path == "/api/status":
                return 200, {
                    "chatgpt_firefox": {
                        "active": False,
                        "state": "inactive",
                        "runtime_mode": "standalone",
                    },
                    "dynamic_workers": {
                        "memory_guard": {
                            "pressure": "ok",
                            "available_mb": 4096,
                            "swap_healthy": True,
                        }
                    },
                }
            if method == "POST" and path == "/api/runner-control":
                return 200, {"ok": True, "status": {"active": True}}
            raise AssertionError((method, path, payload))

        watchdog.api_call = fake_api_call
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = watchdog.run_once(
                    db_path=Path(tmp) / "history.db",
                    state_path=Path(tmp) / "state.json",
                    base_url="http://127.0.0.1:8765",
                )
        finally:
            watchdog.api_call = original_api_call

        self.assertTrue(result["ok"], result)
        self.assertEqual("firefox-runtime-inactive", result["reason"])
        self.assertEqual("restart_firefox", result["global_action"]["action"])
        self.assertTrue(result["global_action"]["ok"])
        self.assertTrue(any(
            method == "POST"
            and path == "/api/runner-control"
            and payload == {"project_id": "", "action": "restart_firefox"}
            for method, path, payload in calls
        ))


if __name__ == "__main__":
    unittest.main()
