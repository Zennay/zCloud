import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_project_activity_timeline as timeline


class ProjectActivityTimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-timeline-")
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
                """
            )
            connection.commit()
        self.now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def add_event(self, minutes, event, project="cloud", worker=1, reason=None, error=None):
        ts = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_events(
                    ts,event,target,title,generating,sending,reason,error,project_id,worker_slot
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    event,
                    "https://chatgpt.com/c/sensitive-id",
                    "raw title must not be emitted",
                    0,
                    0,
                    reason,
                    error,
                    project,
                    worker,
                ),
            )
            connection.commit()

    def add_command(self, minutes, action, status, project="cloud", result="TOP_SECRET_RESULT"):
        created = (self.now - timedelta(minutes=minutes + 1)).isoformat()
        updated = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_commands(
                    project_id,action,status,created_at,updated_at,result
                ) VALUES(?,?,?,?,?,?)""",
                (project, action, status, created, updated, result),
            )
            connection.commit()

    def test_filters_noise_and_emits_meaningful_newest_first(self):
        self.add_event(5, "heartbeat")
        self.add_event(4, "injection-success")
        self.add_event(3, "generation-started", worker=2)
        self.add_event(2, "generation-finished", worker=2)
        self.add_event(1, "send-blocked", worker=2, reason="generation-active")
        payload = timeline.report(self.db, 24, "cloud", 20, self.now)
        self.assertEqual(
            ["send-blocked", "generation-finished", "generation-started"],
            [item["code"] for item in payload["timeline"]],
        )
        self.assertEqual(2, payload["timeline"][0]["worker_slot"])
        self.assertEqual("generation-active", payload["timeline"][0]["reason_code"])

    def test_control_outcome_is_included_without_result_payload(self):
        self.add_command(2, "pause", "completed", result="SECRET_TOKEN=abc")
        payload = timeline.report(self.db, 24, "cloud", 20, self.now)
        entry = payload["timeline"][0]
        self.assertEqual("control:pause:completed", entry["code"])
        self.assertEqual("Pauzeer afgerond", entry["summary"])
        serialized = json.dumps(payload)
        self.assertNotIn("SECRET_TOKEN", serialized)
        self.assertNotIn("result", entry)

    def test_raw_runner_target_title_and_error_are_not_exposed(self):
        self.add_event(1, "startup-blocked", reason="unsafe reason with spaces", error="token=super-secret")
        payload = timeline.report(self.db, 24, "cloud", 20, self.now)
        serialized = json.dumps(payload)
        self.assertNotIn("chatgpt.com", serialized)
        self.assertNotIn("raw title", serialized)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("reason_code", payload["timeline"][0])

    def test_project_filter_prevents_cross_project_entries(self):
        self.add_event(1, "generation-finished", project="cloud")
        self.add_event(1, "generation-finished", project="ftmo")
        self.add_command(1, "start", "completed", project="ftmo")
        payload = timeline.report(self.db, 24, "cloud", 20, self.now)
        self.assertTrue(payload["timeline"])
        self.assertEqual({"cloud"}, {item["project_id"] for item in payload["timeline"]})

    def test_database_is_read_only_and_byte_stable(self):
        self.add_event(1, "generation-finished")
        before = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        timeline.report(self.db, 24, "cloud", 20, self.now)
        after = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        self.assertEqual(before, after)

    def test_symlink_database_is_rejected(self):
        link = Path(self.tmp.name) / "linked.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(ValueError, "symlink"):
            timeline.report(link, 24, "cloud", 20, self.now)

    def test_missing_runner_schema_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        with closing(sqlite3.connect(broken)) as connection:
            connection.execute("CREATE TABLE runner_events(id INTEGER PRIMARY KEY)")
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "missing required columns"):
            timeline.report(broken, 24, "cloud", 20, self.now)

    def test_bounds_are_fail_closed(self):
        with self.assertRaises(ValueError):
            timeline.report(self.db, 0.1, "cloud", 20, self.now)
        with self.assertRaises(ValueError):
            timeline.report(self.db, 24, "cloud", 201, self.now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
