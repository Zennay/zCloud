from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_throughput_tracking_baseline.py"
SPEC = importlib.util.spec_from_file_location("zcloud_throughput_tracking_baseline", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

STARTED_AT = "2026-10-06T10:29:27.005194+00:00"
SHA = "55aef90793cbc6236a37d90f623c48f569be96e3"
SOURCE = "github-actions:zcloud-vps-deploy:37448947435"


class ThroughputTrackingBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-throughput-baseline-")
        root = Path(self.tmp.name)
        self.db = root / "history.db"
        connection = sqlite3.connect(self.db)
        connection.execute(
            """CREATE TABLE project_state_receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                ci_status TEXT NOT NULL,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO project_state_receipts(
                project_id,commit_sha,ci_status,source,observed_at,evidence_json
            ) VALUES(?,?,?,?,?,?)""",
            (
                "cloud",
                SHA,
                "success",
                "github-actions:zcloud-vps-deploy",
                STARTED_AT,
                json.dumps({"workflow_run_id": 37448947435}),
            ),
        )
        connection.commit()
        connection.close()
        self.projects = root / "projects.json"
        self.projects.write_text(
            json.dumps(
                [
                    {"id": "cloud", "status": "active"},
                    {"id": "ftmo"},
                    {"id": "ulab", "status": "archived"},
                    {"id": "cloud", "status": "active"},
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return module.plan_baseline(
            self.db,
            self.projects,
            started_at=STARTED_AT,
            production_sha=SHA,
            source=SOURCE,
        )

    def apply(self, **overrides):
        params = {
            "started_at": STARTED_AT,
            "production_sha": SHA,
            "source": SOURCE,
            "confirm": module.CONFIRM_TOKEN,
        }
        params.update(overrides)
        return module.apply_baseline(
            self.db,
            self.projects,
            **params,
        )

    def test_dry_run_is_byte_and_mtime_read_only(self):
        before_bytes = self.db.read_bytes()
        before_stat = self.db.stat()

        plan = self.plan()

        after_stat = self.db.stat()
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["table_exists"])
        self.assertEqual(["cloud", "ftmo"], plan["projects"])
        self.assertEqual(["cloud", "ftmo"], plan["planned"])
        self.assertEqual([], plan["conflicts"])
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_apply_requires_exact_confirmation(self):
        with self.assertRaisesRegex(ValueError, "confirmation token"):
            self.apply(confirm="WRONG")

        connection = sqlite3.connect(self.db)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (module.TABLE,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(exists)

    def test_apply_creates_active_project_baselines_only(self):
        result = self.apply()

        self.assertTrue(result["applied"])
        self.assertEqual(2, result["created_count"])
        self.assertEqual(["cloud", "ftmo"], result["created"])

        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                f"""SELECT project_id,started_at,source,production_sha
                    FROM {module.TABLE} ORDER BY project_id"""
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(["cloud", "ftmo"], [row["project_id"] for row in rows])
        for row in rows:
            self.assertEqual(STARTED_AT, row["started_at"])
            self.assertEqual(SOURCE, row["source"])
            self.assertEqual(SHA, row["production_sha"])

    def test_same_apply_is_idempotent(self):
        first = self.apply()
        second = self.apply()

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual([], second["created"])
        self.assertEqual(["cloud", "ftmo"], second["unchanged"])

        connection = sqlite3.connect(self.db)
        try:
            count = connection.execute(
                f"SELECT COUNT(*) FROM {module.TABLE}"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(2, count)

    def test_conflicting_reapply_fails_without_overwrite(self):
        self.apply()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.apply(started_at="2026-10-06T10:30:00+00:00")

        connection = sqlite3.connect(self.db)
        try:
            starts = [
                row[0]
                for row in connection.execute(
                    f"SELECT started_at FROM {module.TABLE} ORDER BY project_id"
                ).fetchall()
            ]
        finally:
            connection.close()
        self.assertEqual([STARTED_AT, STARTED_AT], starts)

    def test_existing_conflict_is_visible_in_dry_run(self):
        self.apply()

        plan = module.plan_baseline(
            self.db,
            self.projects,
            started_at="2026-10-06T10:30:00+00:00",
            production_sha=SHA,
            source=SOURCE,
        )

        self.assertEqual([], plan["planned"])
        self.assertEqual(2, len(plan["conflicts"]))
        self.assertEqual(
            {"cloud", "ftmo"},
            {item["project_id"] for item in plan["conflicts"]},
        )

    def test_exact_production_receipt_is_required(self):
        connection = sqlite3.connect(self.db)
        connection.execute("DELETE FROM project_state_receipts")
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(ValueError, "no exact successful"):
            self.plan()

    def test_wrong_production_run_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no exact successful"):
            module.plan_baseline(
                self.db,
                self.projects,
                started_at=STARTED_AT,
                production_sha=SHA,
                source="github-actions:zcloud-vps-deploy:999",
            )

    def test_non_production_source_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source must be"):
            module.plan_baseline(
                self.db,
                self.projects,
                started_at=STARTED_AT,
                production_sha=SHA,
                source="manual",
            )

    def test_invalid_started_at_and_sha_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            module.plan_baseline(
                self.db,
                self.projects,
                started_at="2026-10-06T10:29:27",
                production_sha=SHA,
                source=SOURCE,
            )
        with self.assertRaisesRegex(ValueError, "commit SHA"):
            module.plan_baseline(
                self.db,
                self.projects,
                started_at=STARTED_AT,
                production_sha="abc",
                source=SOURCE,
            )

    def test_future_started_at_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "future"):
            module.plan_baseline(
                self.db,
                self.projects,
                started_at="2999-01-01T00:00:00+00:00",
                production_sha=SHA,
                source=SOURCE,
            )

    def test_existing_table_requires_project_primary_key(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            f"""CREATE TABLE {module.TABLE}(
                project_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                source TEXT NOT NULL,
                production_sha TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(ValueError, "primary key"):
            self.plan()

    def test_symlink_database_and_projects_are_rejected(self):
        db_link = Path(self.tmp.name) / "history-link.db"
        projects_link = Path(self.tmp.name) / "projects-link.json"
        try:
            os.symlink(self.db, db_link)
            os.symlink(self.projects, projects_link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")

        with self.assertRaisesRegex(ValueError, "database path must not be a symlink"):
            module.plan_baseline(
                db_link,
                self.projects,
                started_at=STARTED_AT,
                production_sha=SHA,
                source=SOURCE,
            )
        with self.assertRaisesRegex(ValueError, "projects path must not be a symlink"):
            module.plan_baseline(
                self.db,
                projects_link,
                started_at=STARTED_AT,
                production_sha=SHA,
                source=SOURCE,
            )

    def test_partial_existing_table_fails_closed(self):
        connection = sqlite3.connect(self.db)
        connection.execute(
            f"CREATE TABLE {module.TABLE}(project_id TEXT PRIMARY KEY)"
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(ValueError, "missing required columns"):
            self.plan()


if __name__ == "__main__":
    unittest.main(verbosity=2)
