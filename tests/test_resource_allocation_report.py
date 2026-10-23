import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_resource_allocation_report as allocation


class ResourceAllocationReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-allocation-")
        root = Path(self.tmp.name)
        self.contracts = root / "project-contracts.json"
        self.db = root / "history.db"
        self.now = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
        self.write_contracts()
        self.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def write_contracts(self):
        self.contracts.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "resource_pools": {
                        "protected": {"slots": 2},
                        "heavy": {"slots": 1},
                        "disabled": {"slots": 0},
                    },
                    "projects": {
                        "cloud": {
                            "compute": {
                                "class": "control-plane",
                                "pool": "protected",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 1536,
                                "priority": "normal",
                                "protected": True,
                            }
                        },
                        "ftmo": {
                            "compute": {
                                "class": "research-heavy",
                                "pool": "heavy",
                                "cpu_soft_cores": 4,
                                "memory_soft_mb": 4608,
                                "priority": "high",
                                "protected": False,
                            }
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def init_db(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                """CREATE TABLE resource_leases(
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    workload_class TEXT NOT NULL,
                    cpu_soft_cores REAL NOT NULL,
                    memory_soft_mb INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(project_id,owner_id)
                )"""
            )

    def add_lease(
        self,
        project_id,
        owner_id,
        *,
        pool,
        workload_class,
        cpu,
        memory,
        lease_until=None,
        metadata='{"secret":"never-report-me"}',
    ):
        until = lease_until or (self.now + timedelta(minutes=30)).isoformat()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                """INSERT INTO resource_leases(
                    project_id,owner_id,pool,workload_class,cpu_soft_cores,
                    memory_soft_mb,acquired_at,lease_until,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    owner_id,
                    pool,
                    workload_class,
                    cpu,
                    memory,
                    self.now.isoformat(),
                    until,
                    metadata,
                ),
            )

    def report(self):
        return allocation.build_report(self.contracts, self.db, now=self.now)

    def test_empty_allocation_is_valid_and_explicit(self):
        payload = self.report()
        self.assertTrue(payload["summary"]["contract_consistent"])
        rows = {row["project_id"]: row for row in payload["projects"]}
        self.assertEqual("not_allocated", rows["cloud"]["state"])
        self.assertEqual(0, rows["cloud"]["admitted_allocation"]["active_leases"])

    def test_matching_active_lease_reports_requested_vs_admitted(self):
        self.add_lease(
            "cloud",
            "cloud::w1",
            pool="protected",
            workload_class="control-plane",
            cpu=1,
            memory=1536,
        )
        payload = self.report()
        cloud = next(row for row in payload["projects"] if row["project_id"] == "cloud")
        self.assertEqual("allocated_as_requested", cloud["state"])
        self.assertEqual(1, cloud["requested_per_lease"]["cpu_soft_cores"])
        self.assertEqual(1.0, cloud["admitted_allocation"]["cpu_soft_cores_total"])
        self.assertEqual(1536, cloud["admitted_allocation"]["memory_soft_mb_total"])
        self.assertTrue(payload["summary"]["contract_consistent"])

    def test_expired_leases_are_not_counted(self):
        self.add_lease(
            "cloud",
            "old",
            pool="protected",
            workload_class="control-plane",
            cpu=1,
            memory=1536,
            lease_until=(self.now - timedelta(seconds=1)).isoformat(),
        )
        self.assertEqual(0, self.report()["summary"]["active_lease_count"])

    def test_contract_drift_is_fail_closed_in_summary(self):
        self.add_lease(
            "ftmo",
            "ftmo::trainer",
            pool="protected",
            workload_class="wrong",
            cpu=2,
            memory=1024,
        )
        payload = self.report()
        ftmo = next(row for row in payload["projects"] if row["project_id"] == "ftmo")
        self.assertEqual("allocation_drift", ftmo["state"])
        self.assertEqual(
            ["class_drift", "cpu_budget_drift", "memory_budget_drift", "pool_drift"],
            ftmo["reason_codes"],
        )
        self.assertFalse(payload["summary"]["contract_consistent"])
        self.assertIn("allocation_contract_drift", payload["summary"]["reason_codes"])

    def test_unknown_projects_and_pools_are_reported_without_owner_leak(self):
        self.add_lease(
            "ghost",
            "sensitive-owner",
            pool="mystery",
            workload_class="mystery",
            cpu=9,
            memory=9999,
        )
        payload = self.report()
        self.assertEqual(["ghost"], payload["unknown_lease_projects"])
        self.assertEqual(["mystery"], payload["unknown_lease_pools"])
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("sensitive-owner", encoded)
        self.assertNotIn("never-report-me", encoded)
        self.assertNotIn('"owner_id"', encoded)
        self.assertNotIn('"metadata"', encoded)

    def test_pool_overcommit_is_visible(self):
        for owner in ("a", "b"):
            self.add_lease(
                "ftmo",
                owner,
                pool="heavy",
                workload_class="research-heavy",
                cpu=4,
                memory=4608,
            )
        payload = self.report()
        heavy = next(row for row in payload["pools"] if row["pool"] == "heavy")
        self.assertTrue(heavy["oversubscribed"])
        self.assertEqual(2, heavy["admitted_leases"])
        self.assertEqual(1, payload["summary"]["pool_overcommit_count"])

    def test_malformed_active_timestamp_fails_closed(self):
        self.add_lease(
            "cloud",
            "bad",
            pool="protected",
            workload_class="control-plane",
            cpu=1,
            memory=1536,
            lease_until="not-a-time",
        )
        with self.assertRaisesRegex(ValueError, "invalid_timestamp:lease_until"):
            self.report()

    def test_read_is_non_mutating(self):
        self.add_lease(
            "cloud",
            "cloud::w1",
            pool="protected",
            workload_class="control-plane",
            cpu=1,
            memory=1536,
        )
        before = self.db.stat()
        self.report()
        after = self.db.stat()
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_symlink_inputs_fail_closed(self):
        for source in (self.contracts, self.db):
            backup = source.with_name(source.name + ".real")
            source.rename(backup)
            try:
                os.symlink(backup, source)
            except (OSError, NotImplementedError):
                self.skipTest("symlink unsupported")
            try:
                with self.assertRaisesRegex(ValueError, "symlink_not_allowed"):
                    self.report()
            finally:
                source.unlink()
                backup.rename(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
