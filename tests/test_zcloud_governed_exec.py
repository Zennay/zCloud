import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

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
                            "lane_profile": "research",
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
                            "lane_profile": "ml",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
