"""Offline, two-connection resource admission regressions for zCloud.

The second test is intentionally RED on main@f115d70: callers using SQLite
autocommit can interleave after the pool-count read and both acquire a one-slot
pool. No production queue, VPS, runner, or real leases are touched.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import project_runtime as runtime


class CountBarrierCursor:
    """Pause *after* a slot count has been read to expose the check/insert race."""

    def __init__(self, cursor, barrier):
        self._cursor = cursor
        self._barrier = barrier

    def fetchone(self):
        row = self._cursor.fetchone()
        self._barrier.wait(timeout=10)
        return row


class CountBarrierConnection:
    def __init__(self, connection, barrier):
        self.connection = connection
        self.barrier = barrier

    def execute(self, statement, parameters=()):
        cursor = self.connection.execute(statement, parameters)
        if "SELECT COUNT(*) FROM resource_leases" in statement:
            return CountBarrierCursor(cursor, self.barrier)
        return cursor


class ResourceLeaseAdmissionContentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zcloud-resource-lease-race-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "isolated.sqlite"
        self.contract = self.root / "project-contracts.json"
        self.contract.write_text(json.dumps({
            "schema_version": 1,
            "resource_pools": {"heavy": {"slots": 1}, "control": {"slots": 1}},
            "projects": {
                "ftmo": {
                    "lane_profile": "research-validation",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "heavy", "class": "research-heavy"},
                },
                "haxlab": {
                    "lane_profile": "ml-training",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "heavy", "class": "ml-heavy"},
                },
                "cloud": {
                    "lane_profile": "platform",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"pool": "control", "class": "control-plane"},
                },
            },
        }), encoding="utf-8")
        patcher = mock.patch.object(runtime, "CONTRACT_FILE", self.contract)
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.connect() as connection:
            runtime.init_tables(connection)

    def connect(self, *, autocommit=False):
        connection = sqlite3.connect(
            self.database,
            timeout=10,
            isolation_level=None if autocommit else "DEFERRED",
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def test_normal_transactional_connections_never_overadmit_heavy_pool(self):
        start = threading.Barrier(2)

        def acquire(project_id):
            with self.connect() as connection:
                start.wait(timeout=10)
                result = runtime.acquire_resource(connection, project_id, project_id)
                connection.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(acquire, project) for project in ("ftmo", "haxlab")]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(1, sum(bool(result["acquired"]) for result in results))
        with self.connect() as connection:
            self.assertEqual(1, runtime.resource_status(connection)["pools"]["heavy"]["used"])

    def test_autocommit_connections_must_not_overadmit_one_slot_pool(self):
        """RED on current main: read/count and insert are not one atomic operation."""
        both_counted_empty = threading.Barrier(2)

        def acquire(project_id):
            with self.connect(autocommit=True) as connection:
                wrapped = CountBarrierConnection(connection, both_counted_empty)
                return runtime.acquire_resource(wrapped, project_id, project_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(acquire, project) for project in ("ftmo", "haxlab")]
            results = [future.result(timeout=20) for future in futures]
        self.assertLessEqual(
            sum(bool(result["acquired"]) for result in results), 1,
            "one-slot resource pool must not issue two leases under SQLite autocommit",
        )
        with self.connect() as connection:
            self.assertLessEqual(runtime.resource_status(connection)["pools"]["heavy"]["used"], 1)

    def test_independent_control_pool_not_blocked_by_heavy_lease(self):
        with self.connect() as connection:
            self.assertTrue(runtime.acquire_resource(connection, "ftmo", "ftmo-1")["acquired"])
            connection.commit()
        with self.connect() as connection:
            control = runtime.acquire_resource(connection, "cloud", "cloud-1")
            connection.commit()
            self.assertTrue(control["acquired"])
            status = runtime.resource_status(connection)["pools"]
            self.assertEqual(1, status["heavy"]["used"])
            self.assertEqual(1, status["control"]["used"])


if __name__ == "__main__":
    unittest.main()
