from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_project_throughput_report.py"
SPEC = importlib.util.spec_from_file_location("zcloud_project_throughput_report", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class ProjectThroughputReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        connection = sqlite3.connect(self.db)
        connection.execute(
            """CREATE TABLE project_state_receipts(
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
            )"""
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def add_receipt(
        self,
        project_id,
        source,
        observed_at,
        *,
        ci_status="success",
        evidence=None,
    ):
        connection = sqlite3.connect(self.db)
        connection.execute(
            """INSERT INTO project_state_receipts(
                project_id,ci_status,source,observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (
                project_id,
                ci_status,
                source,
                observed_at,
                json.dumps(evidence if evidence is not None else {}),
                observed_at,
            ),
        )
        connection.commit()
        connection.close()

    def test_counts_only_done_queue_receipts_in_window(self):
        self.add_receipt(
            "cloud",
            "portfolio_queue:q1",
            "2026-10-06T09:00:00+00:00",
            evidence={"queue_id": "q1", "result": "DONE", "evidence": "secret detail"},
        )
        self.add_receipt(
            "cloud",
            "portfolio_queue:q2",
            "2026-10-06T08:00:00+00:00",
            evidence={"queue_id": "q2", "result": "DONE"},
        )
        self.add_receipt(
            "cloud",
            "portfolio_queue:q3",
            "2026-10-06T07:00:00+00:00",
            ci_status="in_progress",
            evidence={"queue_id": "q3", "result": "CONTINUE"},
        )
        self.add_receipt(
            "ftmo",
            "portfolio_queue:q4",
            "2026-10-06T09:30:00+00:00",
            evidence={"queue_id": "q4", "result": "DONE"},
        )
        self.add_receipt(
            "cloud",
            "portfolio_queue:old",
            "2026-10-04T09:00:00+00:00",
            evidence={"queue_id": "old", "result": "DONE"},
        )
        self.add_receipt(
            "cloud",
            "deploy:main",
            "2026-10-06T09:45:00+00:00",
            evidence={"result": "DONE"},
        )

        report = module.build_report(
            self.db,
            hours=24,
            now_value="2026-10-06T10:00:00+00:00",
        )

        self.assertEqual(3, report["total_completed"])
        self.assertEqual(2, report["projects"]["cloud"]["completed"])
        self.assertEqual(1, report["projects"]["ftmo"]["completed"])
        self.assertEqual(
            "2026-10-06T09:00:00+00:00",
            report["projects"]["cloud"]["last_completed_at"],
        )
        self.assertEqual({"2026-10-06": 2}, report["projects"]["cloud"]["daily"])
        self.assertNotIn("secret detail", json.dumps(report))
        self.assertTrue(report["coverage_complete"])

    def test_project_filter_includes_zero_count_project(self):
        self.add_receipt(
            "ftmo",
            "portfolio_queue:q1",
            "2026-10-06T09:00:00+00:00",
            evidence={"queue_id": "q1", "result": "DONE"},
        )

        report = module.build_report(
            self.db,
            hours=24,
            projects=["cloud"],
            now_value="2026-10-06T10:00:00+00:00",
        )

        self.assertEqual(0, report["total_completed"])
        self.assertEqual(["cloud"], sorted(report["projects"]))
        self.assertEqual(0, report["projects"]["cloud"]["completed"])
        self.assertIsNone(report["projects"]["cloud"]["last_completed_at"])

    def test_duplicate_done_receipt_counts_queue_item_once(self):
        self.add_receipt(
            "cloud",
            "portfolio_queue:q1",
            "2026-10-06T08:00:00+00:00",
            evidence={"queue_id": "q1", "result": "DONE"},
        )
        self.add_receipt(
            "cloud",
            "portfolio_queue:q1",
            "2026-10-06T09:00:00+00:00",
            evidence={"queue_id": "q1", "result": "DONE"},
        )

        report = module.build_report(
            self.db,
            hours=24,
            now_value="2026-10-06T10:00:00+00:00",
        )

        self.assertEqual(1, report["total_completed"])
        self.assertEqual(1, report["duplicate_done_receipts"])
        self.assertEqual(
            "2026-10-06T08:00:00+00:00",
            report["projects"]["cloud"]["last_completed_at"],
        )

    def test_malformed_receipt_is_visible_and_not_counted(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            """INSERT INTO project_state_receipts(
                project_id,ci_status,source,observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (
                "cloud",
                "success",
                "portfolio_queue:q1",
                "not-a-time",
                "{broken",
                "2026-10-06T09:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()

        report = module.build_report(
            self.db,
            hours=24,
            now_value="2026-10-06T10:00:00+00:00",
        )

        self.assertFalse(report["coverage_complete"])
        self.assertEqual(1, report["malformed_receipts"])
        self.assertEqual(0, report["total_completed"])

    def test_queue_id_mismatch_is_fail_visible(self):
        self.add_receipt(
            "cloud",
            "portfolio_queue:q1",
            "2026-10-06T09:00:00+00:00",
            evidence={"queue_id": "different", "result": "DONE"},
        )
        report = module.build_report(
            self.db,
            hours=24,
            now_value="2026-10-06T10:00:00+00:00",
        )
        self.assertFalse(report["coverage_complete"])
        self.assertEqual(1, report["malformed_receipts"])
        self.assertEqual(0, report["total_completed"])

    def test_report_is_byte_and_mtime_read_only(self):
        self.add_receipt(
            "cloud",
            "portfolio_queue:q1",
            "2026-10-06T09:00:00+00:00",
            evidence={"queue_id": "q1", "result": "DONE"},
        )
        before_bytes = self.db.read_bytes()
        before_stat = self.db.stat()

        module.build_report(
            self.db,
            hours=24,
            now_value="2026-10-06T10:00:00+00:00",
        )

        after_stat = self.db.stat()
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_symlink_database_is_rejected(self):
        link = Path(self.tmp.name) / "history-link.db"
        try:
            os.symlink(self.db, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            module.build_report(
                link,
                hours=24,
                now_value="2026-10-06T10:00:00+00:00",
            )

    def test_missing_schema_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        connection = sqlite3.connect(broken)
        connection.execute("CREATE TABLE project_state_receipts(id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            module.build_report(
                broken,
                hours=24,
                now_value="2026-10-06T10:00:00+00:00",
            )

    def test_hours_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "between 1 and"):
            module.build_report(self.db, hours=0)
        with self.assertRaisesRegex(ValueError, "between 1 and"):
            module.build_report(self.db, hours=module.MAX_HOURS + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
