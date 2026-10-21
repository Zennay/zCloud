import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_worker_log_view as viewer


class WorkerLogViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-worker-log-")
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
        self.now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def add_event(self, *, minutes: int, slot: int = 1, event: str = "heartbeat", secret: str = ""):
        ts = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_events(
                       ts,event,target,title,generating,sending,reason,error,project_id,worker_slot
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ts, event, f"target-{secret}", f"title-{secret}", 1, 0, f"reason-{secret}", f"error-{secret}", "cloud", slot),
            )
            connection.commit()

    def add_command(self, *, minutes: int, project_id: str = "cloud::w1", action: str = "push", status: str = "completed", secret: str = ""):
        ts = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result)
                   VALUES(?,?,?,?,?,?)""",
                (project_id, action, status, ts, ts, f"result-{secret}"),
            )
            connection.commit()

    def test_combines_worker_events_and_canonical_control_entries(self):
        self.add_event(minutes=5, slot=1, event="heartbeat")
        self.add_event(minutes=4, slot=2, event="generation-finished")
        self.add_command(minutes=3, project_id="cloud::w1", action="push", status="completed")
        self.add_command(minutes=2, project_id="cloud", action="pause", status="completed")
        self.add_command(minutes=1, project_id="cloud::w2", action="push", status="failed")

        payload = viewer.report(
            self.db,
            project="cloud",
            worker_slot=1,
            hours=6,
            limit=20,
            now=self.now,
        )
        self.assertEqual(2, payload["returned"])
        self.assertEqual(["control", "event"], [item["kind"] for item in payload["entries"]])
        self.assertEqual("cloud::w1", payload["worker_key"])
        self.assertEqual({"event": 1, "control": 1}, payload["counts"])

    def test_filters_are_bounded_to_requested_kind_and_value(self):
        self.add_event(minutes=3, event="heartbeat")
        self.add_event(minutes=2, event="prompt-sent")
        self.add_command(minutes=1, action="push", status="completed")

        events = viewer.report(
            self.db,
            project="cloud",
            worker_slot=1,
            hours=6,
            limit=20,
            kind="event",
            event="prompt-sent",
            now=self.now,
        )
        self.assertEqual(["prompt-sent"], [item["event"] for item in events["entries"]])

        controls = viewer.report(
            self.db,
            project="cloud",
            worker_slot=1,
            hours=6,
            limit=20,
            kind="control",
            action="push",
            status="completed",
            now=self.now,
        )
        self.assertEqual(1, controls["returned"])
        self.assertEqual("push", controls["entries"][0]["action"])

    def test_raw_payload_fields_are_never_exposed(self):
        secret = "SUPER_SECRET_PAYLOAD_7421"
        self.add_event(minutes=4, event="send-blocked", secret=secret)
        self.add_command(minutes=3, secret=secret)
        self.add_event(minutes=2, event=f"event {secret}")
        self.add_command(minutes=1, action=f"action {secret}", status=f"status {secret}")
        payload = viewer.report(
            self.db,
            project="cloud",
            worker_slot=1,
            hours=6,
            limit=20,
            now=self.now,
        )
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertGreaterEqual(
            sum(
                1
                for item in payload["entries"]
                if item.get("event") == "unclassified"
                or item.get("action") == "unclassified"
                or item.get("status") == "unclassified"
            ),
            2,
        )
        for forbidden in ("target", "title", "reason", "error", "result", "conversation_id"):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_malformed_relevant_timestamp_fails_closed(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_events(ts,event,generating,sending,project_id,worker_slot)
                   VALUES(?,?,?,?,?,?)""",
                ("9999-not-a-time", "heartbeat", 0, 0, "cloud", 1),
            )
            connection.commit()
        with self.assertRaises(ValueError):
            viewer.report(
                self.db,
                project="cloud",
                worker_slot=1,
                hours=6,
                limit=20,
                now=self.now,
            )

    def test_report_is_byte_and_mtime_read_only(self):
        self.add_event(minutes=1)
        self.add_command(minutes=1)
        before_bytes = self.db.read_bytes()
        before_mtime = self.db.stat().st_mtime_ns
        viewer.report(
            self.db,
            project="cloud",
            worker_slot=1,
            hours=6,
            limit=20,
            now=self.now,
        )
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_mtime, self.db.stat().st_mtime_ns)

    def test_rejects_symlink_database(self):
        link = Path(self.tmp.name) / "history-link.db"
        try:
            os.symlink(self.db, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unsupported")
        with self.assertRaisesRegex(ValueError, "database_symlink_not_allowed"):
            viewer.report(
                link,
                project="cloud",
                worker_slot=1,
                hours=6,
                limit=20,
                now=self.now,
            )

    def test_rejects_unbounded_or_unsafe_filters(self):
        with self.assertRaisesRegex(ValueError, "invalid_worker_slot"):
            viewer.report(self.db, project="cloud", worker_slot=0, now=self.now)
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            viewer.report(self.db, project="cloud", worker_slot=1, limit=201, now=self.now)
        with self.assertRaisesRegex(ValueError, "invalid_project"):
            viewer.report(self.db, project="../cloud", worker_slot=1, now=self.now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
