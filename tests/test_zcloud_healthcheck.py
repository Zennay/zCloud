import importlib.util
import json
import sqlite3
import tempfile
import unittest
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
        )

    def test_green_health_contract(self):
        result = self.evaluate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            {
                "webservice": "healthy",
                "firefox_automation": "healthy",
                "worker_scheduler": "healthy",
                "project_state_store": "healthy",
            },
            result["summary"],
        )

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
        self.assertTrue(any("desired count mismatch" in x for x in scheduler["detail"]))

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

    def test_database_is_opened_read_only(self):
        result = self.store()
        self.assertTrue(result["ok"])
        before = self.db.stat().st_mtime_ns
        self.store()
        self.assertEqual(before, self.db.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
