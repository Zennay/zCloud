"""Offline regression for non-mutating deploy SQLite permission preflight."""

import errno
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_sqlite_permission_preflight_20261013.py"
spec = importlib.util.spec_from_file_location("zcloud_sqlite_permission_preflight", MODULE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SQLitePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.db = self.directory / "history.db"
        self.db.write_bytes(b"not a SQLite database: probe deliberately never opens SQLite")

    def test_writable_metadata_is_not_a_sqlite_connect(self):
        result = probe.snapshot(self.db)
        self.assertTrue(result["ok"])
        self.assertFalse(result["mutations_performed"])
        self.assertFalse(result["sqlite_connection_opened"])
        self.assertEqual(result["schema"], "zcloud-sqlite-permission-preflight-v1")
        json.dumps(result)

    def test_missing_database_denied_without_creation(self):
        self.db.unlink()
        self.assertFalse(probe.snapshot(self.db)["ok"])
        self.assertFalse(self.db.exists())

    def test_missing_sidecars_do_not_count_as_errors(self):
        result = probe.snapshot(self.db)
        self.assertFalse(result["files"]["wal"]["exists"])
        self.assertFalse(result["files"]["shm"]["exists"])
        self.assertNotIn("wal_unwritable_or_unsafe", result["failures"])

    def test_readonly_database_fail_closed(self):
        actual = probe.os.open

        def blocked(path, flags, *args, **kwargs):
            if os.fspath(path) == os.fspath(self.db):
                raise PermissionError(errno.EACCES, "permission denied")
            return actual(path, flags, *args, **kwargs)

        with patch.object(probe.os, "open", side_effect=blocked):
            result = probe.snapshot(self.db)
        self.assertIn("database_unwritable_or_missing", result["failures"])
        self.assertEqual(result["files"]["database"]["open_error"], "EACCES")

    def test_readonly_wal_fail_closed(self):
        wal = Path(str(self.db) + "-wal")
        wal.write_bytes(b"x")
        actual = probe.os.open

        def blocked(path, flags, *args, **kwargs):
            if os.fspath(path) == os.fspath(wal):
                raise PermissionError(errno.EACCES, "permission denied")
            return actual(path, flags, *args, **kwargs)

        with patch.object(probe.os, "open", side_effect=blocked):
            result = probe.snapshot(self.db)
        self.assertIn("wal_unwritable_or_unsafe", result["failures"])

    def test_symlink_database_refused(self):
        real = self.directory / "real.db"
        real.write_bytes(b"original")
        self.db.unlink()
        self.db.symlink_to(real)
        result = probe.snapshot(self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(result["files"]["database"]["error"], "noncanonical_file_kind")

    def test_readonly_filesystem_denied(self):
        original = probe.os.statvfs
        class ReadonlyVFS:
            f_flag = getattr(os, "ST_RDONLY", 1)
        with patch.object(probe.os, "statvfs", return_value=ReadonlyVFS()):
            result = probe.snapshot(self.db)
        self.assertIn("filesystem_unknown_or_readonly", result["failures"])

    def test_stdout_contains_no_database_contents_or_path(self):
        result = probe.snapshot(self.db)
        encoded = json.dumps(result)
        self.assertNotIn("not a SQLite database", encoded)
        self.assertNotIn(str(self.directory), encoded)


if __name__ == "__main__":
    unittest.main()
