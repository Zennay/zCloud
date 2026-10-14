import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_runner_event_retention_audit.py"
SPEC = importlib.util.spec_from_file_location("zcloud_runner_event_retention_audit", MODULE_PATH)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


class RunnerEventRetentionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,
                    target TEXT,
                    title TEXT,
                    reason TEXT,
                    error TEXT,
                    project_id TEXT,
                    worker_slot INTEGER
                )"""
            )

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, ts, event, *, target="secret-target", reason="secret-reason"):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,target,reason) VALUES(?,?,?,?)",
                (ts, event, target, reason),
            )

    def test_aggregate_age_buckets_are_exact_and_sensitive_payload_is_absent(self):
        observed = datetime(2026, 10, 6, 9, 45, tzinfo=timezone.utc)
        self.add((observed - timedelta(days=1)).isoformat(), "heartbeat")
        self.add((observed - timedelta(days=8)).isoformat(), "heartbeat")
        self.add((observed - timedelta(days=31)).isoformat(), "heartbeat")
        self.add((observed - timedelta(days=91)).isoformat().replace("+00:00", "Z"), "prompt-sent")
        self.add("not-a-timestamp", "malformed-ts")

        result = audit.audit(self.db, observed_at=observed)

        self.assertTrue(result["ok"])
        self.assertEqual("read-only", result["mode"])
        self.assertEqual(5, result["runner_events"]["total"])
        self.assertEqual(1, result["runner_events"]["invalid_ts"])
        self.assertEqual(["event", "ts", "id"], result["selected_columns"])
        self.assertFalse(result["sensitive_fields_selected"])
        by_event = {
            item["event"]: item
            for item in result["runner_events"]["events"]
        }
        self.assertEqual(
            {
                "total": 3,
                "older_7d": 2,
                "older_30d": 1,
                "older_90d": 0,
            },
            {
                key: by_event["heartbeat"][key]
                for key in ("total", "older_7d", "older_30d", "older_90d")
            },
        )
        self.assertEqual(1, by_event["prompt-sent"]["older_90d"])
        self.assertEqual(1, by_event["malformed-ts"]["invalid_ts"])
        self.assertEqual(0, by_event["malformed-ts"]["older_90d"])
        self.assertIsNone(by_event["malformed-ts"]["oldest_ts"])
        self.assertIsNone(by_event["malformed-ts"]["newest_ts"])
        encoded = json.dumps(result)
        self.assertNotIn("secret-target", encoded)
        self.assertNotIn("secret-reason", encoded)

    def test_database_is_opened_query_only(self):
        self.add("2026-10-06T09:45:00+00:00", "heartbeat")
        conn = audit._open_ro(self.db)
        try:
            query_only = int(conn.execute("PRAGMA query_only").fetchone()[0])
            self.assertEqual(1, query_only)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO runner_events(ts,event) VALUES(?,?)",
                    ("2026-10-06T09:46:00+00:00", "must-fail"),
                )
        finally:
            conn.close()

    def test_missing_table_and_symlink_path_fail_closed(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(audit.RetentionAuditError, "runner_events table missing"):
            audit.audit(empty)

        link = Path(self.tmp.name) / "history-link.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(audit.RetentionAuditError, "refusing symlink"):
            audit.audit(link)

    def test_cli_outputs_json_without_sensitive_event_payload(self):
        self.add(
            "2026-10-06T09:45:00+00:00",
            "heartbeat",
            target="https://chatgpt.com/c/private-conversation",
            reason="private-reason",
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--db",
                str(self.db),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertNotIn("private-conversation", proc.stdout)
        self.assertNotIn("private-reason", proc.stdout)


if __name__ == "__main__":
    unittest.main()
