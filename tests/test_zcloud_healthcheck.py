import importlib.util
import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_healthcheck.py"
SPEC = importlib.util.spec_from_file_location("zcloud_healthcheck", MODULE_PATH)
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(health)


class ZCloudHealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-health-")
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE runner_targets(
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    worker_count INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE runner_workers(
                    project_id TEXT NOT NULL,
                    worker_slot INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    desired_state TEXT NOT NULL DEFAULT 'running',
                    PRIMARY KEY(project_id,worker_slot)
                );
                CREATE TABLE runner_commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result TEXT
                );
                CREATE TABLE runner_events(id INTEGER PRIMARY KEY);
                CREATE TABLE task_claims(id INTEGER PRIMARY KEY);
                CREATE TABLE improvement_loops(id INTEGER PRIMARY KEY);
                CREATE TABLE config_audit(id INTEGER PRIMARY KEY);
                CREATE TABLE feature_flags(name TEXT PRIMARY KEY);
                CREATE TABLE worker_preflights(project_id TEXT, worker_id TEXT, owner_id TEXT);
            """)
            conn.execute(
                "INSERT INTO runner_targets VALUES(?,?,?,?,?,?)",
                ("cloud", "zCloud", "conv", "prompt", 1, 2),
            )
            conn.execute(
                "INSERT INTO runner_workers VALUES(?,?,?,?)",
                ("cloud", 1, "conv", "running"),
            )
            conn.execute(
                "INSERT INTO runner_workers VALUES(?,?,?,?)",
                ("cloud", 2, "conv2", "paused"),
            )
            conn.commit()
        self.now = datetime(2026, 9, 26, 23, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self):
        status = {
            "errors": [],
            "chatgpt_firefox": {"active": True, "state": "active", "main_pid": 123},
            "dynamic_workers": {
                "memory_guard": {
                    "pressure": "ok",
                    "available_mb": 6144,
                    "headroom_mb": 2048,
                    "effective_headroom_mb": 2048,
                    "new_worker_capacity": 2,
                    "swap_total_mb": 4096,
                    "swap_free_mb": 3072,
                    "swap_healthy": True,
                }
            },
            "chatgpt_runners": {
                "cloud": {
                    "active": True,
                    "desired_worker_count": 2,
                    "active_worker_count": 1,
                    "workers": [
                        {"worker_slot": 1, "desired_state": "running"},
                        {"worker_slot": 2, "desired_state": "paused"},
                    ],
                }
            },
        }
        targets = {
            "max_workers": 8,
            "projects": {
                "cloud::w1": {
                    "project_id": "cloud::w1",
                    "base_project_id": "cloud",
                    "worker_slot": 1,
                },
                "cloud::w2": {
                    "project_id": "cloud::w2",
                    "base_project_id": "cloud",
                    "worker_slot": 2,
                },
            },
        }
        return status, targets

    def store(self):
        return health.inspect_store(self.db, now=self.now)

    def evaluate(self, **kwargs):
        status, targets = self.sample()
        return health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=kwargs.get("zcloud_service", True),
            firefox_service=kwargs.get("firefox_service", True),
            source_runtime_match=kwargs.get("source_runtime_match", True),
            legacy_violentmonkey_only=kwargs.get("legacy_violentmonkey_only", False),
        )

    def test_green_health_contract(self):
        result = self.evaluate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            {
                "webservice": "healthy",
                "firefox_automation": "healthy",
                "worker_scheduler": "healthy",
                "worker_memory": "healthy",
                "project_state_store": "healthy",
            },
            result["summary"],
        )

    def test_critical_worker_memory_fails_health_contract(self):
        status, targets = self.sample()
        status["dynamic_workers"]["memory_guard"].update({
            "pressure": "critical",
            "available_mb": 700,
            "new_worker_capacity": 0,
            "swap_total_mb": 0,
            "swap_free_mb": 0,
            "swap_healthy": False,
        })
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("critical", result["summary"]["worker_memory"])
        check = next(x for x in result["checks"] if x["name"] == "worker_memory")
        self.assertFalse(check["detail"]["swap_healthy"])

    def test_store_fails_closed_on_missing_worker_slot(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "DELETE FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            )
            conn.commit()
        result = self.store()
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing desired worker slots" in x for x in result["problems"]))

    def test_store_fails_closed_on_stale_pending_command(self):
        created = (self.now - timedelta(minutes=10)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                ("cloud", "start", "pending", created, created),
            )
            conn.commit()
        result = self.store()
        self.assertFalse(result["ok"])
        self.assertEqual(1, len(result["stale_pending_commands"]))

    def test_scheduler_detects_count_mismatch(self):
        status, targets = self.sample()
        status["chatgpt_runners"]["cloud"]["desired_worker_count"] = 1
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])
        scheduler = next(x for x in result["checks"] if x["name"] == "worker_scheduler")
        self.assertTrue(
            any("desired count below configured minimum" in x for x in scheduler["detail"])
        )

    def test_scheduler_accepts_dynamic_slots_above_configured_baseline(self):
        status, targets = self.sample()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'"
            )
            conn.commit()

        # A second live slot is valid when the global allocator temporarily grants
        # extra capacity to this project. The configured count is a minimum baseline,
        # not an exact live-allocation snapshot.
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=True,
            source_runtime_match=True,
        )
        self.assertTrue(result["ok"], result)

    def test_scheduler_detects_non_contiguous_dynamic_api_slots(self):
        status, targets = self.sample()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'"
            )
            conn.commit()
        targets["projects"].pop("cloud::w2")
        targets["projects"]["cloud::w3"] = {
            "project_id": "cloud::w3",
            "base_project_id": "cloud",
            "worker_slot": 3,
        }
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])
        scheduler = next(x for x in result["checks"] if x["name"] == "worker_scheduler")
        self.assertTrue(
            any("non-contiguous runner-target slots" in x for x in scheduler["detail"])
        )

    def test_scheduler_detects_missing_api_slot(self):
        status, targets = self.sample()
        targets["projects"].pop("cloud::w2")
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])

    def test_violentmonkey_only_requires_live_standalone_firefox(self):
        status, targets = self.sample()
        status["chatgpt_firefox"] = {
            "active": False,
            "state": "inactive",
            "runtime_mode": "violentmonkey-only",
            "main_pid": 0,
        }
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=False,
            source_runtime_match=True,
            legacy_violentmonkey_only=True,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual("problem", result["summary"]["firefox_automation"])

    def test_violentmonkey_only_accepts_live_standalone_firefox(self):
        status, targets = self.sample()
        status["chatgpt_firefox"] = {
            "active": True,
            "state": "active",
            "runtime_mode": "standalone",
            "main_pid": 123,
        }
        result = health.evaluate(
            status,
            targets,
            self.store(),
            zcloud_service=True,
            firefox_service=False,
            source_runtime_match=True,
            legacy_violentmonkey_only=True,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual("healthy", result["summary"]["firefox_automation"])

    def test_legacy_marker_requires_both_violentmonkey_and_execcondition(self):
        marker = Path(self.tmp.name) / "10-legacy-disabled.conf"
        marker.write_text("# Violentmonkey only\nExecCondition=/bin/false\n", encoding="utf-8")
        self.assertTrue(health.legacy_violentmonkey_only(marker))
        marker.write_text("# Violentmonkey only\n", encoding="utf-8")
        self.assertFalse(health.legacy_violentmonkey_only(marker))

    def test_webservice_and_firefox_fail_independently(self):
        result = self.evaluate(zcloud_service=False)
        self.assertFalse(result["ok"])
        self.assertEqual("problem", result["summary"]["webservice"])
        result = self.evaluate(firefox_service=False)
        self.assertFalse(result["ok"])
        self.assertEqual("problem", result["summary"]["firefox_automation"])
        result = self.evaluate(source_runtime_match=False)
        self.assertFalse(result["ok"])
        self.assertEqual("problem", result["summary"]["firefox_automation"])

    def test_live_health_gives_deep_status_a_bounded_longer_budget(self):
        source_dir = Path(self.tmp.name) / "firefox-extension"
        source_dir.mkdir(parents=True, exist_ok=True)
        source = source_dir / "background.js"
        runtime = Path(self.tmp.name) / "runtime-background.js"
        source.write_text("same-runtime", encoding="utf-8")
        runtime.write_text("same-runtime", encoding="utf-8")
        status, targets = self.sample()
        calls = []

        def fake_http(url, timeout=3.0):
            calls.append((url, timeout))
            return status if url.endswith("/api/status") else targets

        with mock.patch.object(health, "http_json", side_effect=fake_http), mock.patch.object(
            health, "service_active", return_value=True
        ):
            result = health.live_health(
                root=Path(self.tmp.name),
                db_path=self.db,
                base_url="http://zcloud",
                runtime_extension=runtime,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [
                ("http://zcloud/api/status", health.DEEP_STATUS_TIMEOUT_SECONDS),
                ("http://zcloud/api/runner-targets", health.FAST_API_TIMEOUT_SECONDS),
            ],
            calls,
        )
        self.assertGreater(health.DEEP_STATUS_TIMEOUT_SECONDS, health.FAST_API_TIMEOUT_SECONDS)

    def test_database_is_opened_read_only(self):
        result = self.store()
        self.assertTrue(result["ok"])
        before = self.db.stat().st_mtime_ns
        self.store()
        self.assertEqual(before, self.db.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
