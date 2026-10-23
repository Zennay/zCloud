import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_governed_exec",
    ROOT / "scripts" / "zcloud_governed_exec.py",
)
governed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governed)


class GovernedExecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-governed-exec-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.original_contract = runtime.CONTRACT_FILE
        self.contract = self.root / "project-contracts.json"
        self.contract.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "defaults": {"autonomy": {}},
                    "resource_pools": {
                        "protected": {"slots": 2},
                        "heavy": {"slots": 1},
                    },
                    "projects": {
                        "cloud": {
                            "queue_mode": "execution",
                            "lane_profile": "platform",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "control-plane",
                                "pool": "protected",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 512,
                            },
                        },
                        "ftmo": {
                            "queue_mode": "execution",
                            "lane_profile": "research-validation",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "research-heavy",
                                "pool": "heavy",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 1024,
                            },
                        },
                        "haxlab": {
                            "queue_mode": "execution",
                            "lane_profile": "ml-training",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "ml-heavy",
                                "pool": "heavy",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 1024,
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        runtime.CONTRACT_FILE = self.contract

    def tearDown(self):
        runtime.CONTRACT_FILE = self.original_contract
        self.tmp.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        runtime.init_tables(conn)
        return conn

    def test_busy_heavy_pool_fails_closed_before_child_starts(self):
        conn = self._connect()
        self.assertTrue(runtime.acquire_resource(conn, "ftmo", "holder")["acquired"])
        conn.commit()
        marker = self.root / "should-not-exist.txt"

        rc = governed.run_governed(
            self.db,
            "haxlab",
            "contender",
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"],
        )

        self.assertEqual(75, rc)
        self.assertFalse(marker.exists())
        runtime.release_resource(conn, "ftmo", "holder")
        conn.commit()
        conn.close()

    def test_successful_child_inherits_identity_and_releases_lease(self):
        marker = self.root / "identity.json"
        code = (
            "import json,os; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(json.dumps({{'project':os.environ['ZCLOUD_RESOURCE_PROJECT'],"
            "'owner':os.environ['ZCLOUD_RESOURCE_OWNER']}))"
        )
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-success",
            [sys.executable, "-c", code],
        )

        self.assertEqual(0, rc)
        self.assertEqual(
            {"project": "ftmo", "owner": "run-success"},
            json.loads(marker.read_text()),
        )
        conn = self._connect()
        self.assertEqual(0, runtime.resource_status(conn)["pools"]["heavy"]["used"])
        conn.close()

    def test_failed_child_preserves_exit_code_and_releases_lease(self):
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-failure",
            [sys.executable, "-c", "raise SystemExit(7)"],
        )

        self.assertEqual(7, rc)
        conn = self._connect()
        self.assertEqual([], runtime.resource_status(conn)["leases"])
        conn.close()

    @unittest.skipUnless(
        hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"),
        "Linux affinity APIs required",
    )
    def test_child_receives_contract_cpu_and_memory_bounds(self):
        marker = self.root / "limits.json"
        code = (
            "import json,os,resource; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(json.dumps({{'cpus':len(os.sched_getaffinity(0)),"
            "'memory':resource.getrlimit(resource.RLIMIT_AS)[0]}))"
        )
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-limits",
            [sys.executable, "-c", code],
        )

        self.assertEqual(0, rc)
        limits = json.loads(marker.read_text())
        self.assertEqual(1, limits["cpus"])
        self.assertEqual(1024 * 1024 * 1024, limits["memory"])


    def test_dedicated_heavy_leaf_memory_does_not_hide_parent_pressure(self):
        mib = 1024 * 1024
        values = {
            "/proc/self/cgroup": "0::/user.slice/heavy.service\n",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/user.slice/memory.max": "max",
            "/sys/fs/cgroup/user.slice/heavy.service/memory.max": str(4608 * mib),
            "/sys/fs/cgroup/user.slice/heavy.service/memory.current": str(64 * mib),
        }

        class FakePath:
            def __init__(self, value):
                self.value = str(value)

            def __truediv__(self, other):
                return FakePath(self.value.rstrip("/") + "/" + str(other))

            def read_text(self):
                return values[self.value]

        with mock.patch.object(governed, "Path", FakePath):
            self.assertEqual(
                (4608, 4608, 4608),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )
            self.assertEqual(
                (4608, 4544, None),
                governed._effective_cgroup_memory_limits_mb(11000),
            )
            values["/sys/fs/cgroup/user.slice/memory.max"] = str(2048 * mib)
            values["/sys/fs/cgroup/user.slice/memory.current"] = "0"
            self.assertEqual(
                (2048, 2048, 4608),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )
            values["/sys/fs/cgroup/user.slice/memory.max"] = str(6000 * mib)
            values["/sys/fs/cgroup/user.slice/heavy.service/memory.max"] = str(3072 * mib)
            self.assertEqual(
                (3072, 3072, 3072),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )

    def _expired_lease_connection(self, metadata):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        runtime.init_tables(conn)
        conn.execute(
            "INSERT INTO resource_leases ("
            "project_id,owner_id,pool,workload_class,cpu_soft_cores,"
            "memory_soft_mb,acquired_at,lease_until,metadata_json"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "ftmo", "crashed-worker", "heavy", "research-heavy", 1.0,
                1024, "2020-01-01T00:00:00+00:00",
                "2020-01-01T00:01:00+00:00", json.dumps(metadata),
            ),
        )
        conn.commit()
        return conn

    def test_expired_foreign_heavy_lease_never_uses_local_pid_probe(self):
        conn = self._expired_lease_connection({
            "pid": 43210, "host_boot_id": "different-vps-boot",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_process_group_has_live_members") as probe:
                    self.assertTrue(
                        governed._expired_heavy_group_active(
                            conn, live_authority=True
                        )
                    )
                    probe.assert_not_called()
        finally:
            conn.close()

    def test_expired_heavy_lease_detects_detached_child_in_own_service(self):
        conn = self._expired_lease_connection({
            "executor": "zcloud-governed-exec",
            "host_boot_id": "current-boot",
            "pid": 43210,
            "service_cgroup": "/user.slice/research.service",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_process_group_has_live_members", return_value=False):
                    with mock.patch.object(governed, "_live_service_members", return_value=True):
                        self.assertTrue(
                            governed._expired_heavy_group_active(
                                conn, live_authority=True
                            )
                        )
        finally:
            conn.close()

    def test_expired_governed_prelaunch_lease_checks_cgroup_without_pid(self):
        conn = self._expired_lease_connection({
            "executor": "zcloud-governed-exec",
            "host_boot_id": "current-boot",
            "service_cgroup": "/user.slice/research.service",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_live_service_members", return_value=True):
                    self.assertTrue(
                        governed._expired_heavy_group_active(
                            conn, live_authority=True
                        )
                    )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
