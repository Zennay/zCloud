from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_action_success_ratio", ROOT / "scripts" / "zcloud_action_success_ratio.py"
)
ratio = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ratio)


class ActionSuccessRatioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE runner_commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    action TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    result TEXT
                )"""
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, project, action, status, updated_at, result="SECRET_RESULT"):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)",
                (project, action, status, updated_at, updated_at, result),
            )

    def now(self):
        return datetime(2026, 10, 6, 23, 0, tzinfo=timezone.utc)

    def test_groups_only_terminal_commands_by_action(self):
        for status in ("completed", "completed", "failed", "pending"):
            self.insert("cloud", "push", status, "2026-10-06T22:00:00+00:00")
        self.insert("cloud", "pause", "completed", "2026-10-06T22:30:00+00:00")
        report = ratio.build_report(self.db, hours=24, now=self.now())
        by_action = {item["action"]: item for item in report["actions"]}
        self.assertEqual(
            {"action": "push", "attempts": 3, "completed": 2, "failed": 1, "success_ratio": 0.6667},
            by_action["push"],
        )
        self.assertEqual(1.0, by_action["pause"]["success_ratio"])
        self.assertEqual(4, report["summary"]["attempts"])

    def test_window_excludes_old_terminal_commands(self):
        self.insert("cloud", "push", "failed", "2026-10-04T22:00:00+00:00")
        self.insert("cloud", "push", "completed", "2026-10-06T22:00:00+00:00")
        report = ratio.build_report(self.db, hours=24, now=self.now())
        self.assertEqual(1, report["summary"]["attempts"])
        self.assertEqual(1.0, report["summary"]["success_ratio"])

    def test_project_filter_is_sql_scoped(self):
        self.insert("cloud", "push", "completed", "2026-10-06T22:00:00+00:00")
        self.insert("ftmo", "push", "failed", "2026-10-06T22:00:00+00:00")
        report = ratio.build_report(self.db, hours=24, project="cloud", now=self.now())
        self.assertEqual(1, report["summary"]["attempts"])
        self.assertEqual(0, report["summary"]["failed"])

    def test_result_text_is_never_emitted(self):
        self.insert("cloud", "push", "failed", "2026-10-06T22:00:00+00:00", result="SUPER_SECRET")
        report = ratio.build_report(self.db, hours=24, now=self.now())
        self.assertNotIn("SUPER_SECRET", str(report))

    def test_malformed_action_is_fail_visible_and_omitted(self):
        self.insert("cloud", "bad action with spaces", "failed", "2026-10-06T22:00:00+00:00")
        report = ratio.build_report(self.db, hours=24, now=self.now())
        self.assertFalse(report["coverage_complete"])
        self.assertEqual(1, report["malformed_rows"])
        self.assertEqual([], report["actions"])

    def test_read_only_projection_does_not_modify_database_bytes(self):
        self.insert("cloud", "push", "completed", "2026-10-06T22:00:00+00:00")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        ratio.build_report(self.db, hours=24, now=self.now())
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_bounds_and_symlink_are_fail_closed(self):
        with self.assertRaises(ValueError):
            ratio.build_report(self.db, hours=0, now=self.now())
        with self.assertRaises(ValueError):
            ratio.build_report(self.db, hours=2161, now=self.now())
        link = Path(self.tmp.name) / "linked.db"
        link.symlink_to(self.db)
        with self.assertRaises(ratio.RatioError):
            ratio.build_report(link, now=self.now())


if __name__ == "__main__":
    unittest.main()
