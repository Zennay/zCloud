import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_since_visit_report as delta


class SinceVisitReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-since-visit-")
        self.db = Path(self.tmp.name) / "history.db"
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""CREATE TABLE project_state_receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                commit_sha TEXT NOT NULL DEFAULT '',
                ci_status TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                next_gate TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )""")
            c.execute("""CREATE TABLE runner_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event TEXT NOT NULL,
                reason TEXT,
                error TEXT,
                project_id TEXT,
                worker_slot INTEGER
            )""")
            c.commit()
        self.now = datetime(2026, 10, 6, 19, 0, tzinfo=timezone.utc)
        self.since = (self.now - timedelta(hours=4)).isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def add_receipt(self, project, minutes, *, phase="validation", ci="success", sha="a"*40,
                    action="safe action", blocker=""):
        ts = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""INSERT INTO project_state_receipts(
                project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (project, phase, action, sha, ci, blocker, "", "test", ts, "{}", ts))
            c.commit()

    def add_event(self, project, minutes, event, *, reason="", error=""):
        ts = (self.now - timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""INSERT INTO runner_events(ts,event,reason,error,project_id,worker_slot)
                         VALUES(?,?,?,?,?,?)""", (ts, event, reason, error, project, 1))
            c.commit()

    def test_compact_delta_aggregates_only_meaningful_changes(self):
        self.add_receipt("cloud", 120)
        self.add_event("cloud", 90, "heartbeat")
        self.add_event("cloud", 80, "generation-finished")
        self.add_event("cloud", 70, "portfolio-queue-result")
        payload = delta.report(self.db, self.since, now=self.now)
        self.assertEqual("since-visit-v1", payload["schema_version"])
        self.assertEqual(1, payload["project_count"])
        project = payload["projects"][0]
        self.assertEqual("cloud", project["project_id"])
        self.assertEqual(
            {"generation_finished": 1, "state_receipt": 1, "task_result": 1},
            project["counts"],
        )
        self.assertEqual(3, project["change_count"])
        self.assertEqual("task_result", project["latest_change"])
        self.assertEqual("success", project["latest_receipt"]["ci_status"])
        self.assertEqual("a"*12, project["latest_receipt"]["commit"])

    def test_before_since_and_unknown_runner_events_are_excluded(self):
        self.add_receipt("cloud", 300)
        self.add_event("cloud", 250, "generation-finished")
        self.add_event("cloud", 30, "heartbeat")
        payload = delta.report(self.db, self.since, now=self.now)
        self.assertEqual([], payload["projects"])

    def test_absolute_time_filter_handles_non_utc_offsets(self):
        before = "2026-10-06T16:00:00+02:00"
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""INSERT INTO project_state_receipts(
                project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("cloud", "validation", "", "a"*40, "success", "", "", "test", before, "{}", before))
            c.commit()
        payload = delta.report(self.db, self.since, now=self.now)
        self.assertEqual([], payload["projects"])

    def test_future_runtime_rows_are_excluded(self):
        future = (self.now + timedelta(hours=1)).isoformat()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""INSERT INTO project_state_receipts(
                project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("cloud", "validation", "", "a"*40, "success", "", "", "test", future, "{}", future))
            c.execute("""INSERT INTO runner_events(ts,event,reason,error,project_id,worker_slot)
                         VALUES(?,?,?,?,?,?)""", (future, "generation-finished", "", "", "cloud", 1))
            c.commit()
        payload = delta.report(self.db, self.since, now=self.now)
        self.assertEqual([], payload["projects"])

    def test_project_filter_is_exact_and_bounded(self):
        self.add_receipt("cloud", 60)
        self.add_receipt("ftmo", 30)
        payload = delta.report(self.db, self.since, project="cloud", now=self.now)
        self.assertEqual(["cloud"], [p["project_id"] for p in payload["projects"]])
        with self.assertRaises(ValueError):
            delta.report(self.db, self.since, project="../cloud", now=self.now)

    def test_raw_sensitive_values_and_keys_never_appear(self):
        marker = "SUPER_SECRET_TOKEN_123"
        self.add_receipt(
            "cloud", 60, action=marker, blocker=marker,
            phase=marker, ci=marker, sha=marker,
        )
        self.add_event("cloud", 30, "startup-blocked", reason=marker, error=marker)
        payload = delta.report(self.db, self.since, now=self.now)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(marker, serialized)
        forbidden = {"action", "blocker", "phase", "reason", "error", "evidence_json", "next_gate", "prompt"}
        self.assertTrue(forbidden.isdisjoint(payload.keys()))
        for project in payload["projects"]:
            self.assertTrue(forbidden.isdisjoint(project.keys()))
            self.assertTrue(forbidden.isdisjoint(project["latest_receipt"].keys()))
        project = payload["projects"][0]
        self.assertEqual("other", project["latest_receipt"]["ci_status"])
        self.assertIsNone(project["latest_receipt"]["commit"])

    def test_database_is_read_only(self):
        self.add_receipt("cloud", 60)
        before = self.db.stat().st_mtime_ns
        delta.report(self.db, self.since, now=self.now)
        after = self.db.stat().st_mtime_ns
        self.assertEqual(before, after)

    def test_since_and_now_must_be_timezone_aware_not_future_and_bounded(self):
        with self.assertRaises(ValueError):
            delta.report(self.db, "2026-10-06T18:00:00", now=self.now)
        with self.assertRaises(ValueError):
            delta.report(self.db, (self.now + timedelta(minutes=2)).isoformat(), now=self.now)
        with self.assertRaises(ValueError):
            delta.report(self.db, (self.now - timedelta(days=32)).isoformat(), now=self.now)
        with self.assertRaises(ValueError):
            delta.report(self.db, self.since, now=datetime(2026, 10, 6, 19, 0))

    def test_missing_required_schema_fails_closed(self):
        bad = Path(self.tmp.name) / "bad.db"
        with closing(sqlite3.connect(bad)) as c:
            c.execute("CREATE TABLE runner_events(id INTEGER PRIMARY KEY, ts TEXT, event TEXT, project_id TEXT)")
            c.commit()
        with self.assertRaises(RuntimeError):
            delta.report(bad, self.since, now=self.now)

    def test_row_budget_marks_report_truncated(self):
        for i in range(3):
            self.add_event("cloud", 30-i, "generation-finished")
        payload = delta.report(self.db, self.since, now=self.now, row_limit=2)
        self.assertTrue(payload["truncated"])
        self.assertEqual(2, payload["projects"][0]["counts"]["generation_finished"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
