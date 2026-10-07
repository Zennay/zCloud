import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_action_outcome_report.py"
SPEC = importlib.util.spec_from_file_location("zcloud_action_outcome_report", SCRIPT)
assert SPEC and SPEC.loader
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class ActionOutcomeReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                CREATE TABLE runner_commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    action TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    result TEXT
                )
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, action, status, age_minutes=1, result="opaque"):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO runner_commands(
                    project_id,action,status,created_at,updated_at,result
                ) VALUES(?,?,?,?,?,?)
                """,
                ("cloud", action, status, ts, ts, result),
            )

    def test_reports_terminal_success_failure_ratio_per_action(self):
        self.add("start", "completed")
        self.add("start", "completed")
        self.add("start", "failed")
        self.add("start", "pending")
        self.add("push", "failed")

        report = REPORT.build_report(self.db, 24)
        actions = {item["action"]: item for item in report["actions"]}

        self.assertEqual(4, actions["start"]["commands"])
        self.assertEqual(3, actions["start"]["terminal"])
        self.assertEqual(66.67, actions["start"]["success_rate_pct"])
        self.assertEqual(33.33, actions["start"]["failure_rate_pct"])
        self.assertEqual(1, actions["start"]["pending"])
        self.assertEqual(0.0, actions["push"]["success_rate_pct"])
        self.assertEqual(100.0, actions["push"]["failure_rate_pct"])
        self.assertTrue(report["data_quality"]["timestamp_coverage_complete"])

    def test_pending_and_unknown_statuses_do_not_pollute_terminal_ratio(self):
        self.add("pause", "completed")
        self.add("pause", "pending")
        self.add("pause", "mystery")

        report = REPORT.build_report(self.db, 24)
        pause = report["actions"][0]

        self.assertEqual(3, pause["commands"])
        self.assertEqual(1, pause["terminal"])
        self.assertEqual(1, pause["pending"])
        self.assertEqual(1, pause["unknown_status"])
        self.assertEqual(100.0, pause["success_rate_pct"])

    def test_window_excludes_old_commands(self):
        self.add("start", "failed", age_minutes=60 * 30)
        self.add("start", "completed", age_minutes=10)

        report = REPORT.build_report(self.db, 24)

        self.assertEqual(1, report["totals"]["commands"])
        self.assertEqual(1, report["totals"]["completed"])
        self.assertEqual(0, report["totals"]["failed"])

    def test_unparseable_timestamps_are_disclosed_not_silently_counted(self):
        self.add("start", "completed")
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO runner_commands(
                    project_id,action,status,created_at,updated_at,result
                ) VALUES(?,?,?,?,?,?)
                """,
                ("cloud", "start", "failed", "not-a-time", "", "opaque"),
            )

        report = REPORT.build_report(self.db, 24)

        self.assertFalse(report["data_quality"]["timestamp_coverage_complete"])
        self.assertEqual(1, report["data_quality"]["unparseable_timestamp_rows"])
        self.assertEqual(1, report["totals"]["commands"])
        self.assertEqual(1, report["totals"]["completed"])
        self.assertEqual(0, report["totals"]["failed"])

    def test_read_only_report_does_not_mutate_database_bytes_or_metadata(self):
        self.add("drain", "completed")
        before_bytes = self.db.read_bytes()
        before_stat = self.db.stat()

        REPORT.build_report(self.db, 24)

        after_stat = self.db.stat()
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_missing_schema_fails_closed(self):
        bad = Path(self.tmp.name) / "bad.db"
        with sqlite3.connect(bad) as conn:
            conn.execute("CREATE TABLE runner_commands(action TEXT,status TEXT)")

        with self.assertRaises(REPORT.ReportError):
            REPORT.build_report(bad, 24)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlink_database_is_rejected(self):
        linked = Path(self.tmp.name) / "linked.db"
        linked.symlink_to(self.db)

        with self.assertRaises(REPORT.ReportError):
            REPORT.build_report(linked, 24)

    def test_cli_emits_json_without_result_payload(self):
        self.add("new_chat", "failed", result="secret-like diagnostic payload")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.db), "--hours", "24"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertNotIn("secret-like diagnostic payload", proc.stdout)
        self.assertEqual(
            ["action", "status", "created_at", "updated_at"],
            payload["semantics"]["payload_fields_read"],
        )


if __name__ == "__main__":
    unittest.main()
