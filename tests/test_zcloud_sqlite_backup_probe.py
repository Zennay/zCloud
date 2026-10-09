"""Recovery probe integration tests using disposable real SQLite producers."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import project_runtime as runtime
from scripts import zcloud_sqlite_backup_probe as probe


class SQLiteBackupProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "history # & ?.db"
        self.writer = sqlite3.connect(self.db)
        self.writer.row_factory = sqlite3.Row
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        runtime.init_tables(self.writer)
        runtime.record_receipt(self.writer, "cloud", phase="Verifying", action="backup canary",
                               evidence={"password": "fixture-secret-must-not-leak"})
        runtime.acquire_resource(self.writer, "cloud", "fixture-owner-private",
                                 metadata={"token": "fixture-token-private"})
        self.writer.commit()

    def rows(self):
        return {
            table: [tuple(row) for row in self.writer.execute("SELECT * FROM " + table)]
            for table in ("project_state_receipts", "resource_leases")
        }

    def test_real_wal_receipt_and_lease_are_readable_without_source_changes(self):
        before = self.rows()
        report = probe.probe_backup(self.db)
        self.assertTrue(report["readable"])
        self.assertEqual(report["row_counts"]["project_state_receipts"], 1)
        self.assertEqual(report["row_counts"]["resource_leases"], 1)
        self.assertEqual(before, self.rows())
        self.assertFalse(report["source_rows_modified"])
        self.assertFalse(report["production_restore_performed"])
        self.assertFalse(report["authority_granted"])

    def test_uncommitted_writer_rows_are_excluded(self):
        runtime.record_receipt(self.writer, "cloud", action="uncommitted-secret")
        report = probe.probe_backup(self.db)
        self.assertTrue(report["readable"])
        self.assertEqual(report["row_counts"]["project_state_receipts"], 1)
        self.writer.rollback()

    def test_expired_leases_are_preserved_without_cleanup(self):
        self.writer.execute("UPDATE resource_leases SET lease_until='2000-01-01T00:00:00+00:00'")
        self.writer.commit()
        before = self.rows()
        report = probe.probe_backup(self.db)
        self.assertEqual(1, report["row_counts"]["resource_leases"])
        self.assertEqual(before, self.rows())

    def test_report_never_contains_row_payloads_or_source_path(self):
        text = json.dumps(probe.probe_backup(self.db))
        for secret in ("fixture-secret", "fixture-token", "fixture-owner", "backup canary",
                       str(self.db), "evidence", "metadata"):
            self.assertNotIn(secret, text)
        self.assertEqual(len(probe.probe_backup(self.db)["schema_sha256"]), 64)

    def test_missing_source_does_not_create_database(self):
        absent = Path(self.tmp.name) / "missing.db"
        with self.assertRaises(ValueError):
            probe.probe_backup(absent)
        self.assertFalse(absent.exists())

    def test_bounds_reject_invalid_values_without_opening_sqlite(self):
        for options in ({"max_pages": True}, {"max_pages": 0}, {"max_pages": 10001},
                        {"timeout_seconds": True}, {"timeout_seconds": float("nan")},
                        {"timeout_seconds": float("inf")}, {"timeout_seconds": 0},
                        {"timeout_seconds": 31}):
            with self.subTest(options=options), patch.object(probe.sqlite3, "connect") as connect:
                with self.assertRaises(ValueError):
                    probe.probe_backup(self.db, **options)
                connect.assert_not_called()

    def test_size_bound_preserves_all_source_rows(self):
        before = self.rows()
        with self.assertRaises(ValueError):
            probe.probe_backup(self.db, max_pages=1)
        self.assertEqual(before, self.rows())

    def test_time_budget_aborts_without_source_changes(self):
        before = self.rows()
        with patch.object(probe.time, "monotonic", side_effect=[0, 10]):
            with self.assertRaises(ValueError):
                probe.probe_backup(self.db, timeout_seconds=1)
        self.assertEqual(before, self.rows())

    def test_missing_required_schema_fails_closed(self):
        other = Path(self.tmp.name) / "uninitialized.db"
        with sqlite3.connect(other) as conn:
            conn.execute("CREATE TABLE harmless(id INTEGER)")
        report = probe.probe_backup(other)
        self.assertFalse(report["readable"])
        self.assertEqual(["project_state_receipts", "resource_leases"],
                         report["required_tables_missing"])

    def test_foreign_key_corruption_is_unreadable(self):
        self.writer.executescript(
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE child(id INTEGER REFERENCES parent(id));"
            "INSERT INTO child VALUES(777);")
        report = probe.probe_backup(self.db)
        self.assertFalse(report["readable"])
        self.assertFalse(report["foreign_keys_ok"])
        self.assertNotIn("777", json.dumps(report))

    def test_cli_returns_generic_corruption_error(self):
        bad = Path(self.tmp.name) / "bad-secret-path.db"
        bad.write_bytes(b"this is not sqlite private data")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(2, probe.main(["--db", str(bad)]))
        report = json.loads(out.getvalue())
        self.assertEqual("probe_unavailable", report["reason"])
        self.assertNotIn(str(bad), out.getvalue())
        self.assertNotIn("private data", out.getvalue())

    def test_cli_success_is_observation_only(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, probe.main(["--db", str(self.db)]))
        self.assertTrue(json.loads(out.getvalue())["readable"])


if __name__ == "__main__":
    unittest.main()
