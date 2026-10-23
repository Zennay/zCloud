"""Contract tests for resource-lease CLI owner validation (temporary SQLite only)."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from scripts import zcloud_runtime as cli
import project_runtime as runtime


class ResourceCLIOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-cli-owner-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.contract = self.root / "project-contracts.json"
        self.contract.write_text(json.dumps({
            "schema_version": 1,
            "resource_pools": {"heavy": {"slots": 1}},
            "projects": {
                "ftmo": {
                    "lane_profile": "research-validation", "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "heavy", "class": "research-heavy"}
                }
            }
        }), encoding="utf-8")
        patcher = mock.patch.object(runtime, "CONTRACT_FILE", self.contract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, operation, owner):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db), operation, "--project", "ftmo", "--owner", owner
            ])
        payload = json.loads(out.getvalue())
        return rc, payload

    def test_invalid_acquire_identity_never_creates_database(self):
        for invalid in ("", " ", "\t\n", "x" * 201, "é" * 201, "safe\x00unsafe"):
            with self.subTest(invalid_length=len(invalid), invalid_prefix=repr(invalid[:8])):
                with self.assertRaisesRegex(ValueError, r"--owner must be"):
                    self.invoke("acquire", invalid)
                self.assertFalse(self.db.exists(), "invalid owner must not open/write SQLite")

    def test_invalid_release_identity_never_creates_database(self):
        for invalid in ("", "  ", "z" * 201, "id\x00other"):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaisesRegex(ValueError, r"--owner must be"):
                    self.invoke("release", invalid)
                self.assertFalse(self.db.exists(), "invalid release must not open SQLite")

    def test_invalid_acquire_metadata_never_opens_database(self):
        for raw in ("{invalid", "[]", "null", "true", "123", '"text"'):
            with self.subTest(metadata=raw):
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    with redirect_stdout(io.StringIO()):
                        cli.main([
                            "--db", str(self.db), "acquire", "--project", "ftmo",
                            "--owner", "valid", "--metadata-json", raw
                        ])
                self.assertFalse(self.db.exists(), "invalid metadata must not initialize SQLite")

    def test_oversize_metadata_rejected_before_database_creation(self):
        # The underlying runtime silently truncates metadata_json to 4000 chars.
        for blob in ("x" * 4100, "é" * 4100, "é" * 3000):
            with self.subTest(kind=blob[0]):
                with self.assertRaisesRegex(ValueError, "exceeds 4000"):
                    with redirect_stdout(io.StringIO()):
                        cli.main([
                            "--db", str(self.db), "acquire", "--project", "ftmo",
                            "--owner", "worker", "--metadata-json",
                            json.dumps({"blob": blob}, ensure_ascii=False),
                        ])
                self.assertFalse(self.db.exists(), "oversize metadata must not open SQLite")

    def test_nonfinite_metadata_is_rejected_before_database_creation(self):
        for bad_json in ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'):
            with self.subTest(value=bad_json):
                with self.assertRaises(ValueError):
                    with redirect_stdout(io.StringIO()):
                        cli.main([
                            "--db", str(self.db), "acquire", "--project", "ftmo",
                            "--owner", "worker", "--metadata-json", bad_json,
                        ])
                self.assertFalse(self.db.exists(), "non-finite JSON must not touch SQLite")

    def test_maximum_serialized_metadata_is_preserved(self):
        # Canonical separators mean {\\"blob\\":\\"\\"} is 11 chars.
        blob = "a" * (4000 - len('{"blob":""}'))
        with redirect_stdout(io.StringIO()) as out:
            rc = cli.main([
                "--db", str(self.db), "acquire", "--project", "ftmo",
                "--owner", "worker", "--metadata-json", json.dumps({"blob": blob}),
            ])
        self.assertEqual(0, rc)
        self.assertEqual(blob, json.loads(out.getvalue())["lease"]["metadata"]["blob"])

    def test_valid_acquire_metadata_survives_round_trip(self):
        with redirect_stdout(io.StringIO()) as output:
            rc = cli.main([
                "--db", str(self.db), "acquire", "--project", "ftmo",
                "--owner", "valid", "--metadata-json", '{"source":"offline-test"}'
            ])
        self.assertEqual(0, rc)
        self.assertEqual(
            {"source": "offline-test"},
            json.loads(output.getvalue())["lease"]["metadata"],
        )

    def test_exact_two_hundred_character_owner_is_preserved_and_releasable(self):
        owner = "é" * 200
        rc, acquired = self.invoke("acquire", owner)
        self.assertEqual(0, rc)
        self.assertTrue(acquired["acquired"])
        self.assertFalse(acquired["renewed"])
        self.assertEqual(owner, acquired["lease"]["owner_id"])
        rc, renewed = self.invoke("acquire", owner)
        self.assertEqual(0, rc)
        self.assertTrue(renewed["renewed"])
        self.assertEqual(owner, renewed["lease"]["owner_id"])
        rc, released = self.invoke("release", owner)
        self.assertEqual(0, rc)
        self.assertTrue(released["released"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0])

    def test_oversized_owner_rejected_without_changing_existing_lease(self):
        valid_owner = "shared-prefix-" + "a" * 186
        self.assertEqual(200, len(valid_owner))
        rc, first = self.invoke("acquire", valid_owner)
        self.assertEqual(0, rc)
        self.assertEqual(valid_owner, first["lease"]["owner_id"])
        for operation in ("acquire", "release"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "--owner must be"):
                    self.invoke(operation, valid_owner + "suffix")
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("SELECT owner_id FROM resource_leases").fetchall()
            self.assertEqual([(valid_owner,)], rows)
        self.assertEqual(0, self.invoke("release", valid_owner)[0])

    def test_different_owner_respects_busy_pool_after_valid_acquire(self):
        self.assertEqual(0, self.invoke("acquire", "worker-1")[0])
        rc, busy = self.invoke("acquire", "worker-2")
        self.assertEqual(75, rc)
        self.assertFalse(busy["acquired"])
        self.assertEqual("resource_pool_busy", busy["reason"])


if __name__ == "__main__":
    unittest.main()
