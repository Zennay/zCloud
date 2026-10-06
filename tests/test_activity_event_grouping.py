import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_activity_event_grouping as grouping


class ActivityEventGroupingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-event-grouping-")
        self.db = Path(self.tmp.name) / "history.db"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executescript(
                """
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    target TEXT,
                    title TEXT,
                    generating INTEGER,
                    sending INTEGER,
                    reason TEXT,
                    tab_id INTEGER,
                    error TEXT,
                    project_id TEXT,
                    progress_at TEXT,
                    assistant_chars INTEGER,
                    worker_slot INTEGER NOT NULL DEFAULT 1
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
                CREATE TABLE events(
                    id TEXT PRIMARY KEY,
                    ts TEXT,
                    project TEXT,
                    kind TEXT,
                    title TEXT,
                    detail TEXT
                );
                """
            )
            connection.commit()
        self.now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def add_event(self, seconds_ago, event="send-blocked", reason="generation-active"):
        ts = (self.now - timedelta(seconds=seconds_ago)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                "INSERT INTO runner_events(ts,event,reason,project_id,worker_slot) VALUES(?,?,?,?,?)",
                (ts, event, reason, "cloud", 1),
            )
            connection.commit()

    def test_duplicate_semantics_group_inside_window_but_not_outside(self):
        self.add_event(150)
        self.add_event(140)
        self.add_event(20)
        payload = grouping.report(self.db, 1, "cloud", 50, 60, self.now)
        blocked = [group for group in payload["groups"] if group["code"] == "send-blocked"]
        self.assertEqual(2, len(blocked))
        self.assertEqual([1, 2], sorted(group["count"] for group in blocked))
        self.assertEqual(1, payload["duplicates_collapsed"])

    def test_fingerprint_ignores_timestamp_but_includes_worker_identity(self):
        base = {
            "ts": self.now.isoformat(),
            "project_id": "cloud",
            "source": "runner_event",
            "category": "problem",
            "code": "send-blocked",
            "summary": "Prompt geblokkeerd",
            "worker_slot": 1,
            "reason_code": "generation-active",
        }
        later = dict(base, ts=(self.now + timedelta(seconds=5)).isoformat())
        other_worker = dict(later, worker_slot=2)
        fingerprint = grouping.semantic_fingerprint(base)
        self.assertEqual(64, len(fingerprint))
        self.assertEqual(fingerprint, grouping.semantic_fingerprint(later))
        self.assertNotEqual(fingerprint, grouping.semantic_fingerprint(other_worker))

    def test_sanitized_source_prevents_raw_payload_leakage(self):
        ts = (self.now - timedelta(seconds=10)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_events(
                    ts,event,target,title,reason,error,project_id,worker_slot
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    "startup-blocked",
                    "https://chatgpt.com/c/private",
                    "SECRET_TITLE",
                    "generation-active",
                    "SECRET_ERROR",
                    "cloud",
                    1,
                ),
            )
            connection.commit()
        payload = grouping.report(self.db, 1, "cloud", 50, 60, self.now)
        rendered = str(payload)
        self.assertNotIn("chatgpt.com", rendered)
        self.assertNotIn("SECRET_TITLE", rendered)
        self.assertNotIn("SECRET_ERROR", rendered)

    def test_database_is_byte_and_mtime_stable(self):
        self.add_event(10)
        before = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        grouping.report(self.db, 1, "cloud", 50, 60, self.now)
        after = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        self.assertEqual(before, after)

    def test_group_window_bounds_fail_closed(self):
        with self.assertRaises(ValueError):
            grouping.group_events([], 0)
        with self.assertRaises(ValueError):
            grouping.group_events([], 3601)


if __name__ == "__main__":
    unittest.main(verbosity=2)
