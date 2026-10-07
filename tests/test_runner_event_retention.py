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
MODULE_PATH = ROOT / "scripts" / "zcloud_runner_event_retention.py"
SPEC = importlib.util.spec_from_file_location("zcloud_runner_event_retention", MODULE_PATH)
retention = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(retention)


class RunnerEventRetentionTests(unittest.TestCase):
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
                    reason TEXT,
                    project_id TEXT,
                    worker_slot INTEGER
                )"""
            )

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, observed, *, hours, event, ts=None):
        value = ts if ts is not None else (observed - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,target,reason) VALUES(?,?,?,?)",
                (value, event, "sensitive-target", "sensitive-reason"),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT id,event,ts FROM runner_events ORDER BY id"
            ).fetchall()

    def test_dry_run_only_selects_allowlisted_rows_older_than_eight_days(self):
        observed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
        old_heartbeat = self.add(observed, hours=193, event="heartbeat")
        self.add(observed, hours=191, event="heartbeat")
        old_targets = self.add(observed, hours=300, event="targets-loaded")
        self.add(observed, hours=400, event="send-blocked")
        self.add(observed, hours=500, event="heartbeat", ts="not-a-timestamp")

        before = self.rows()
        result = retention.run_retention(
            self.db,
            apply=False,
            confirm="",
            retention_hours=192,
            batch_limit=5000,
            observed_at=observed,
        )

        self.assertEqual("dry-run", result["mode"])
        # The 193h heartbeat is old enough, but it is not the latest heartbeat.
        self.assertEqual(1, result["eligible"]["count"])
        self.assertEqual({"heartbeat": 1}, result["eligible"]["by_event"])
        self.assertEqual(1, result["planned"]["count"])
        self.assertEqual({"heartbeat": 1}, result["planned"]["by_event"])
        self.assertFalse(result["planned"]["has_more"])
        self.assertEqual(old_heartbeat, result["planned"]["oldest_id"])
        self.assertEqual(before, self.rows())
        self.assertNotIn(old_targets, {
            result["planned"]["oldest_id"], result["planned"]["newest_id"]
        })
        self.assertTrue(result["policy"]["malformed_timestamps_preserved"])
        self.assertTrue(result["policy"]["non_allowlisted_events_preserved"])

    def test_latest_row_per_allowlisted_event_is_preserved_even_when_old(self):
        observed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
        older = self.add(observed, hours=500, event="targets-loaded")
        latest = self.add(observed, hours=400, event="targets-loaded")

        result = retention.run_retention(
            self.db,
            apply=True,
            confirm=retention.CONFIRM_TOKEN,
            retention_hours=192,
            batch_limit=5000,
            observed_at=observed,
        )

        self.assertEqual(1, result["deleted"]["count"])
        self.assertEqual({"targets-loaded": 1}, result["deleted"]["by_event"])
        self.assertEqual([(latest, "targets-loaded")], [
            (row[0], row[1]) for row in self.rows()
        ])
        self.assertNotEqual(older, latest)

    def test_apply_requires_confirmation_and_rechecks_exact_predicates(self):
        observed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
        self.add(observed, hours=500, event="heartbeat")
        self.add(observed, hours=1, event="heartbeat")

        with self.assertRaisesRegex(retention.RetentionError, "requires --confirm"):
            retention.run_retention(
                self.db,
                apply=True,
                confirm="wrong",
                retention_hours=192,
                batch_limit=5000,
                observed_at=observed,
            )

        result = retention.run_retention(
            self.db,
            apply=True,
            confirm=retention.CONFIRM_TOKEN,
            retention_hours=192,
            batch_limit=5000,
            observed_at=observed,
        )
        self.assertEqual(1, result["deleted"]["count"])
        remaining = self.rows()
        self.assertEqual(1, len(remaining))
        self.assertEqual("heartbeat", remaining[0][1])

    def test_retention_floor_and_batch_limit_fail_closed(self):
        observed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
        for n in range(5):
            self.add(observed, hours=500 + n, event="heartbeat")
        self.add(observed, hours=1, event="heartbeat")

        with self.assertRaisesRegex(retention.RetentionError, "between 192 and"):
            retention.run_retention(
                self.db,
                apply=False,
                confirm="",
                retention_hours=191,
                batch_limit=5000,
                observed_at=observed,
            )
        with self.assertRaisesRegex(retention.RetentionError, "between 1 and 10000"):
            retention.run_retention(
                self.db,
                apply=False,
                confirm="",
                retention_hours=192,
                batch_limit=10001,
                observed_at=observed,
            )

        result = retention.run_retention(
            self.db,
            apply=False,
            confirm="",
            retention_hours=192,
            batch_limit=2,
            observed_at=observed,
        )
        self.assertEqual(5, result["eligible"]["count"])
        self.assertEqual({"heartbeat": 5}, result["eligible"]["by_event"])
        self.assertEqual(2, result["planned"]["count"])
        self.assertTrue(result["planned"]["has_more"])

    def test_non_allowlisted_semantic_events_and_sensitive_payload_are_preserved(self):
        observed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
        protected = [
            "send-blocked",
            "prompt-sent",
            "generation-started",
            "generation-finished",
            "conversation-adopted",
            "assignment-invalid",
        ]
        for event in protected:
            self.add(observed, hours=1000, event=event)

        result = retention.run_retention(
            self.db,
            apply=True,
            confirm=retention.CONFIRM_TOKEN,
            retention_hours=192,
            batch_limit=5000,
            observed_at=observed,
        )

        self.assertEqual(0, result["deleted"]["count"])
        self.assertEqual(protected, [row[1] for row in self.rows()])
        encoded = json.dumps(result)
        self.assertNotIn("sensitive-target", encoded)
        self.assertNotIn("sensitive-reason", encoded)

    def test_dry_run_connection_is_query_only_and_symlink_is_rejected(self):
        conn = retention.open_db(self.db, apply=False)
        try:
            self.assertEqual(1, int(conn.execute("PRAGMA query_only").fetchone()[0]))
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO runner_events(ts,event) VALUES(?,?)",
                    ("2026-10-06T10:00:00+00:00", "heartbeat"),
                )
        finally:
            conn.close()

        link = Path(self.tmp.name) / "history-link.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(retention.RetentionError, "refusing symlink"):
            retention.run_retention(
                link,
                apply=False,
                confirm="",
                retention_hours=192,
                batch_limit=5000,
            )

    def test_cli_defaults_to_dry_run(self):
        observed = datetime.now(timezone.utc)
        self.add(observed, hours=500, event="heartbeat")
        self.add(observed, hours=1, event="heartbeat")
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--db", str(self.db)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("dry-run", payload["mode"])
        self.assertEqual(192, payload["retention_hours"])


if __name__ == "__main__":
    unittest.main()
