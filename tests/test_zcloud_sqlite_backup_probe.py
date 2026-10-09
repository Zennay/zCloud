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

    def test_backup_preserves_full_receipt_and_lease_payloads(self):
        expected = self.rows()
        copied = []
        real_connect = sqlite3.connect

        class CopyConnection(sqlite3.Connection):
            def close(copy):
                copied.append({
                    table: list(copy.execute("SELECT * FROM " + table))
                    for table in ("project_state_receipts", "resource_leases")
                })
                super().close()

        def connect(database, **kwargs):
            if database == ":memory:":
                kwargs["factory"] = CopyConnection
            return real_connect(database, **kwargs)

        with patch.object(probe.sqlite3, "connect", side_effect=connect):
            self.assertTrue(probe.probe_backup(self.db)["readable"])
        self.assertEqual([expected], copied)

    def test_concurrent_source_growth_aborts_copy_and_preserves_writer_commit(self):
        self.writer.execute("CREATE TABLE growth_canary(payload BLOB)")
        self.writer.execute("INSERT INTO growth_canary VALUES(zeroblob(524288))")
        self.writer.commit()
        limit = self.writer.execute("PRAGMA page_count").fetchone()[0] + 2
        real_connect = sqlite3.connect
        grown = []

        class GrowingSource(sqlite3.Connection):
            def backup(source, destination, *, pages, progress, sleep):
                def grow_then_check(status, remaining, total):
                    if not grown and remaining:
                        self.writer.execute("INSERT INTO growth_canary VALUES(zeroblob(1048576))")
                        self.writer.commit()
                        grown.append(True)
                    progress(status, remaining, total)
                return super().backup(destination, pages=pages,
                                      progress=grow_then_check, sleep=sleep)

        def connect(database, **kwargs):
            if database != ":memory:":
                kwargs["factory"] = GrowingSource
            return real_connect(database, **kwargs)

        with patch.object(probe.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(ValueError, "exceeded"):
                probe.probe_backup(self.db, max_pages=limit)
        self.assertEqual([True], grown)
        self.assertEqual(2, self.writer.execute("SELECT COUNT(*) FROM growth_canary").fetchone()[0])
        self.assertEqual(1, len(self.rows()["project_state_receipts"]))

    def test_source_connection_is_read_only_even_when_caller_attempts_write(self):
        real_connect = sqlite3.connect
        attempts = []

        class ReadOnlySource(sqlite3.Connection):
            def backup(source, destination, **kwargs):
                with self.assertRaises(sqlite3.OperationalError):
                    source.execute("DELETE FROM resource_leases")
                attempts.append(True)
                return super().backup(destination, **kwargs)

        def connect(database, **kwargs):
            if database != ":memory:":
                self.assertTrue(kwargs["uri"])
                self.assertTrue(database.endswith("?mode=ro"))
                kwargs["factory"] = ReadOnlySource
            return real_connect(database, **kwargs)

        before = self.rows()
        with patch.object(probe.sqlite3, "connect", side_effect=connect):
            self.assertTrue(probe.probe_backup(self.db)["readable"])
        self.assertEqual([True], attempts)
        self.assertEqual(before, self.rows())

    def test_optional_queue_and_claim_state_is_counted_without_replay(self):
        self.writer.executescript(
            "CREATE TABLE portfolio_queue(queue_id TEXT PRIMARY KEY,status TEXT,evidence TEXT);"
            "CREATE TABLE task_claims(claim_key TEXT PRIMARY KEY,lease_until TEXT,metadata_json TEXT);"
            "INSERT INTO portfolio_queue VALUES('private-queue','done','private-evidence');"
            "INSERT INTO task_claims VALUES('private-claim','2000-01-01','private-metadata');")
        before = list(self.writer.execute("SELECT * FROM portfolio_queue"))
        report = probe.probe_backup(self.db)
        self.assertEqual(1, report["row_counts"]["portfolio_queue"])
        self.assertEqual(1, report["row_counts"]["task_claims"])
        self.assertEqual(before, list(self.writer.execute("SELECT * FROM portfolio_queue")))
        self.assertFalse(report["authority_granted"])
        self.assertNotIn("private-", json.dumps(report))

    def test_many_foreign_key_failures_return_bounded_observation(self):
        self.writer.executescript(
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE child(id INTEGER REFERENCES parent(id));")
        self.writer.executemany("INSERT INTO child VALUES(?)", [(i,) for i in range(20000)])
        self.writer.commit()
        report = probe.probe_backup(self.db)
        self.assertFalse(report["readable"])
        self.assertFalse(report["foreign_keys_ok"])
        self.assertLess(len(json.dumps(report)), 1000)

    def test_byte_budget_rejects_large_pages_before_creating_copy(self):
        large = Path(self.tmp.name) / "large-page.db"
        with sqlite3.connect(large) as conn:
            conn.execute("PRAGMA page_size=65536")
            conn.execute("CREATE TABLE padding(payload BLOB)")
            conn.execute("INSERT INTO padding VALUES(zeroblob(41943040))")
        before = large.stat().st_size
        real_connect = sqlite3.connect
        opened = []

        def connect(database, **kwargs):
            opened.append(database)
            return real_connect(database, **kwargs)

        with patch.object(probe.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(ValueError, "exceeded"):
                probe.probe_backup(large)
        self.assertEqual(1, len(opened), "oversize input must not open a destination")
        self.assertNotIn(":memory:", opened)
        self.assertEqual(before, large.stat().st_size)

    def test_mid_backup_transaction_never_produces_torn_cross_table_state(self):
        self.writer.execute("CREATE TABLE snapshot_padding(payload BLOB)")
        self.writer.execute("INSERT INTO snapshot_padding VALUES(zeroblob(524288))")
        self.writer.execute("UPDATE project_state_receipts SET phase='old'")
        self.writer.execute("UPDATE resource_leases SET workload_class='old'")
        self.writer.commit()
        real_connect = sqlite3.connect
        committed = []
        copied = []

        class AtomicSource(sqlite3.Connection):
            def backup(source, destination, *, pages, progress, sleep):
                def mutate_then_check(status, remaining, total):
                    if not committed and remaining:
                        self.writer.execute("UPDATE project_state_receipts SET phase='new'")
                        self.writer.execute("UPDATE resource_leases SET workload_class='new'")
                        self.writer.commit()
                        committed.append(True)
                    progress(status, remaining, total)
                return super().backup(destination, pages=pages,
                                      progress=mutate_then_check, sleep=sleep)

        class CapturedCopy(sqlite3.Connection):
            def close(copy):
                copied.append((
                    copy.execute("SELECT phase FROM project_state_receipts").fetchone()[0],
                    copy.execute("SELECT workload_class FROM resource_leases").fetchone()[0]))
                super().close()

        def connect(database, **kwargs):
            kwargs["factory"] = CapturedCopy if database == ":memory:" else AtomicSource
            return real_connect(database, **kwargs)

        with patch.object(probe.sqlite3, "connect", side_effect=connect):
            self.assertTrue(probe.probe_backup(self.db)["readable"])
        self.assertEqual([True], committed)
        self.assertEqual(1, len(copied))
        self.assertIn(copied[0], (("old", "old"), ("new", "new")))

    def test_exclusive_rollback_journal_writer_fails_closed_then_recovers(self):
        locked = Path(self.tmp.name) / "locked-private.db"
        with sqlite3.connect(locked) as conn:
            runtime.init_tables(conn)
        locker = sqlite3.connect(locked)
        locker.row_factory = sqlite3.Row
        try:
            locker.execute("BEGIN EXCLUSIVE")
            runtime.record_receipt(locker, "cloud", action="uncommitted-private")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(2, probe.main(["--db", str(locked), "--timeout-seconds", "0.01"]))
            report = json.loads(out.getvalue())
            self.assertEqual("probe_unavailable", report["reason"])
            self.assertFalse(report["authority_granted"])
            self.assertNotIn(str(locked), out.getvalue())
            self.assertNotIn("uncommitted-private", out.getvalue())
            locker.rollback()
            report = probe.probe_backup(locked)
            self.assertTrue(report["readable"])
            self.assertEqual(0, report["row_counts"]["project_state_receipts"])
            self.assertEqual(0, report["row_counts"]["resource_leases"])
        finally:
            locker.close()


if __name__ == "__main__":
    unittest.main()
