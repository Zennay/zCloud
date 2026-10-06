from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_central_eventstream", ROOT / "scripts" / "zcloud_central_eventstream.py"
)
eventstream = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(eventstream)


class CentralEventStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,
                    project_id TEXT,
                    worker_slot INTEGER
                );
                CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    phase TEXT,
                    ci_status TEXT,
                    blocker TEXT,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE config_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    config_key TEXT NOT NULL,
                    target TEXT NOT NULL,
                    result TEXT NOT NULL
                );
                CREATE TABLE events(
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    project TEXT,
                    kind TEXT NOT NULL,
                    title TEXT,
                    detail TEXT
                );
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert_fixture(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot) VALUES(?,?,?,?)",
                ("2026-10-06T22:04:04+00:00", "heartbeat", "cloud", 1),
            )
            conn.execute(
                "INSERT INTO project_state_receipts(project_id,phase,ci_status,blocker,observed_at) VALUES(?,?,?,?,?)",
                ("cloud", "validating", "green", "SECRET_BLOCKER", "2026-10-06T22:04:03+00:00"),
            )
            conn.execute(
                "INSERT INTO config_audit(ts,config_key,target,result) VALUES(?,?,?,?)",
                ("2026-10-06T22:04:02+00:00", "runner.worker_count", "cloud", "succeeded"),
            )
            conn.execute(
                "INSERT INTO events(id,ts,project,kind,title,detail) VALUES(?,?,?,?,?,?)",
                ("evt-1", "2026-10-06T22:04:01+00:00", "cloud", "deployment", "SECRET_TITLE", "SECRET_DETAIL"),
            )
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot) VALUES(?,?,?,?)",
                ("2026-10-06T22:04:05+00:00", "heartbeat", "ftmo", 2),
            )

    def test_merges_existing_sources_in_timestamp_order_without_free_text(self):
        self.insert_fixture()
        snapshot = eventstream.build_snapshot(self.db, limit=10)
        self.assertTrue(snapshot["coverage_complete"])
        self.assertEqual(
            ["runner_events", "runner_events", "project_state_receipts", "config_audit", "events"],
            [item["source"] for item in snapshot["events"]],
        )
        encoded = json.dumps(snapshot)
        for secret in ("SECRET_BLOCKER", "SECRET_TITLE", "SECRET_DETAIL"):
            self.assertNotIn(secret, encoded)
        receipt = next(x for x in snapshot["events"] if x["source"] == "project_state_receipts")
        self.assertTrue(receipt["blocker_present"])

    def test_project_filter_is_applied_at_each_supported_source(self):
        self.insert_fixture()
        snapshot = eventstream.build_snapshot(self.db, limit=10, project="cloud")
        self.assertTrue(snapshot["events"])
        self.assertTrue(all(item["project_id"] == "cloud" for item in snapshot["events"]))
        self.assertNotIn("ftmo", json.dumps(snapshot))

    def test_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            eventstream.build_snapshot(self.db, limit=0)
        with self.assertRaises(ValueError):
            eventstream.build_snapshot(self.db, limit=501)

    def test_missing_source_is_explicit_and_not_invented(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE config_audit")
        snapshot = eventstream.build_snapshot(self.db, limit=10)
        self.assertFalse(snapshot["coverage_complete"])
        self.assertEqual(["config_audit"], snapshot["missing_sources"])

    def test_malformed_timestamp_is_redacted_and_marks_coverage_incomplete(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot) VALUES(?,?,?,?)",
                ("not-a-time", "heartbeat", "cloud", 1),
            )
        snapshot = eventstream.build_snapshot(self.db, limit=10)
        self.assertFalse(snapshot["coverage_complete"])
        self.assertEqual(1, snapshot["malformed_rows"]["runner_events"])
        self.assertEqual([], snapshot["events"])

    def test_read_only_projection_does_not_modify_database_bytes(self):
        self.insert_fixture()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        eventstream.build_snapshot(self.db, limit=10, project="cloud")
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_symlink_database_is_refused(self):
        link = self.root / "linked.db"
        link.symlink_to(self.db)
        with self.assertRaises(eventstream.EventStreamError):
            eventstream.build_snapshot(link, limit=10)

    def test_invalid_project_identifier_is_refused(self):
        with self.assertRaises(ValueError):
            eventstream.build_snapshot(self.db, project="../cloud")


if __name__ == "__main__":
    unittest.main()
