"""Offline resource CLI transaction and receipt input boundaries.

All database paths and project contracts are temporary; no live VPS or queue is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import project_runtime as runtime
from scripts import zcloud_runtime as cli


class ResourceCLIReceiptTransactionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="zcloud-cli-receipt-txn-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "isolated.sqlite"
        contract = self.root / "contracts.json"
        contract.write_text(json.dumps({
            "schema_version": 1,
            "resource_pools": {"heavy": {"slots": 1}},
            "projects": {
                "ftmo": {
                    "lane_profile": "research-validation", "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "heavy", "class": "heavy"},
                },
                "haxlab": {
                    "lane_profile": "ml-training", "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "heavy", "class": "heavy"},
                },
            },
        }), encoding="utf-8")
        patcher = mock.patch.object(runtime, "CONTRACT_FILE", contract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            return_code = cli.main(["--db", str(self.db), *args])
        return return_code, json.loads(out.getvalue())

    def test_invalid_receipt_json_never_opens_sqlite(self):
        for raw in ('{broken', '[]', 'null', '"text"', 'NaN',
                    '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.invoke("receipt", "--project", "ftmo", "--evidence-json", raw)
                self.assertFalse(self.db.exists())

    def test_oversize_utf8_receipt_json_never_opens_sqlite(self):
        raw = json.dumps({"blob": "é" * 7000}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "receipt evidence exceeds"):
            self.invoke("receipt", "--project", "ftmo", "--evidence-json", raw)
        self.assertFalse(self.db.exists())

    def test_valid_twelve_thousand_byte_receipt_round_trips(self):
        blob = "r" * (12000 - len('{"blob":""}'))
        rc, response = self.invoke(
            "receipt", "--project", "ftmo",
            "--ci-status", "success",
            "--evidence-json", json.dumps({"blob": blob}),
        )
        self.assertEqual(rc, 0)
        self.assertTrue(response["ok"])
        self.assertEqual(blob, response["receipt"]["evidence"]["blob"])
        with sqlite3.connect(self.db) as connection:
            row = connection.execute(
                "SELECT evidence_json FROM project_state_receipts"
            ).fetchone()[0]
        self.assertEqual(12000, len(row.encode("utf-8")))
        self.assertEqual({"blob": blob}, json.loads(row))

    def test_invalid_project_never_opens_sqlite(self):
        for command in ("receipt", "acquire", "release"):
            for project in ("", " ", "\x00", "ftmo\x00shadow"):
                with self.subTest(command=command, project=repr(project)):
                    args = [command, "--project", project]
                    if command != "receipt":
                        args += ["--owner", "valid"]
                    with self.assertRaisesRegex(ValueError, "--project must be"):
                        self.invoke(*args)
                    self.assertFalse(self.db.exists())

    def test_unknown_project_preflight_never_creates_sqlite(self):
        for command in ("receipt", "acquire"):
            with self.subTest(command=command):
                args = [command, "--project", "unknown-project"]
                if command == "acquire":
                    args += ["--owner", "worker"]
                with self.assertRaisesRegex(ValueError, "missing explicit runtime contract"):
                    self.invoke(*args)
                self.assertFalse(self.db.exists())

    def test_invalid_receipt_ci_status_never_opens_sqlite(self):
        for status in ("green", "pending", "success ", "UNKNOWN"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "unsupported ci_status"):
                    self.invoke(
                        "receipt", "--project", "ftmo", "--ci-status", status,
                    )
                self.assertFalse(self.db.exists())

    def test_oversize_or_nul_receipt_fields_never_open_sqlite(self):
        field_limits = {
            "phase": 240, "action": 1000, "commit": 80,
            "blocker": 1000, "next-gate": 1000, "source": 300,
        }
        for field, limit in field_limits.items():
            for invalid in ("x" * (limit + 1), "valid\x00hidden"):
                with self.subTest(field=field, length=len(invalid)):
                    with self.assertRaisesRegex(ValueError, "NUL-free"):
                        self.invoke(
                            "receipt", "--project", "ftmo",
                            "--" + field, invalid,
                        )
                    self.assertFalse(self.db.exists())

    def test_exact_receipt_field_limits_round_trip_without_truncation(self):
        field_limits = {
            "phase": 240, "action": 1000, "commit": 80,
            "blocker": 1000, "next_gate": 1000, "source": 300,
        }
        for field, limit in field_limits.items():
            value = "r" * limit
            with self.subTest(field=field, length=limit):
                rc, response = self.invoke(
                    "receipt", "--project", "ftmo",
                    "--" + field.replace("_", "-"), value,
                )
                self.assertEqual(0, rc)
                self.assertEqual(value, response["receipt"][field])
        with sqlite3.connect(self.db) as connection:
            rows = connection.execute(
                "SELECT phase, action, commit_sha, blocker, next_gate, source "
                "FROM project_state_receipts ORDER BY id"
            ).fetchall()
        self.assertEqual(6, len(rows))
        # Every persisted row preserves its original field at its exact limit.
        for index, (_, limit) in enumerate(field_limits.items()):
            self.assertEqual(limit, len(rows[index][index]))

    def test_acquire_and_release_hold_explicit_write_transaction(self):
        acquire = runtime.acquire_resource
        release = runtime.release_resource
        seen = []

        def check_acquire(conn, *args, **kwargs):
            self.assertTrue(conn.in_transaction)
            seen.append("acquire")
            return acquire(conn, *args, **kwargs)

        def check_release(conn, *args, **kwargs):
            self.assertTrue(conn.in_transaction)
            seen.append("release")
            return release(conn, *args, **kwargs)

        with mock.patch.object(runtime, "acquire_resource", side_effect=check_acquire):
            self.assertEqual(0, self.invoke(
                "acquire", "--project", "ftmo", "--owner", "worker"
            )[0])
        with mock.patch.object(runtime, "release_resource", side_effect=check_release):
            self.assertEqual(0, self.invoke(
                "release", "--project", "ftmo", "--owner", "worker"
            )[0])
        self.assertEqual(["acquire", "release"], seen)

    def test_two_parallel_independent_cli_connections_do_not_overadmit(self):
        started = threading.Barrier(2)

        def request(project):
            started.wait(timeout=10)
            # stdout is shared between threads; return the integer exit code.
            return cli.main([
                "--db", str(self.db), "acquire", "--project", project,
                "--owner", "concurrent-" + project,
            ])

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(request, name) for name in ("ftmo", "haxlab")]
            codes = [future.result(timeout=25) for future in futures]
        self.assertEqual([0, 75], sorted(codes))
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                1, connection.execute(
                    "SELECT COUNT(*) FROM resource_leases"
                ).fetchone()[0],
            )

    def test_failed_acquire_releases_lock_for_next_operation(self):
        with mock.patch.object(runtime, "acquire_resource",
                               side_effect=RuntimeError("fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.invoke("acquire", "--project", "ftmo", "--owner", "failure")
        self.assertEqual(0, self.invoke(
            "acquire", "--project", "ftmo", "--owner", "recovery"
        )[0])


if __name__ == "__main__":
    unittest.main()
