import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import project_runtime as runtime


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-project-runtime-")
        self.original = runtime.CONTRACT_FILE
        self.contracts = Path(self.tmp.name) / "project-contracts.json"
        self.contracts.write_text(json.dumps({
            "schema_version": 1,
            "defaults": {"autonomy": {"dispatch_mode": "vps", "min_ai_interval_seconds": 120}},
            "resource_pools": {
                "protected": {"slots": 2},
                "heavy": {"slots": 1},
                "disabled": {"slots": 0},
            },
            "projects": {
                "cloud": {
                    "queue_mode": "execution",
                    "lane_profile": "platform",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"class": "control-plane", "pool": "protected", "cpu_soft_cores": 1, "memory_soft_mb": 512},
                },
                "ftmo": {
                    "queue_mode": "execution",
                    "lane_profile": "research",
                    "ai_worker_cap": 2,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"class": "research-heavy", "pool": "heavy", "cpu_soft_cores": 4, "memory_soft_mb": 4096},
                },
                "haxlab": {
                    "queue_mode": "execution",
                    "lane_profile": "ml",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"class": "ml-heavy", "pool": "heavy", "cpu_soft_cores": 1, "memory_soft_mb": 2048},
                },
                "ulab": {
                    "queue_mode": "human-gated",
                    "lane_profile": "human-gated",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "external_gate", "auto_start": False},
                    "compute": {"class": "human-gated", "pool": "disabled", "cpu_soft_cores": 0, "memory_soft_mb": 0},
                },
            },
        }), encoding="utf-8")
        runtime.CONTRACT_FILE = self.contracts
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        runtime.init_tables(self.conn)

    def tearDown(self):
        self.conn.close()
        runtime.CONTRACT_FILE = self.original
        self.tmp.cleanup()

    def test_ai_worker_cap_is_contract_driven(self):
        self.assertEqual(2, runtime.ai_worker_cap("ftmo", 3))
        self.assertEqual(1, runtime.ai_worker_cap("cloud", 3))

    def test_receipt_overrides_stale_phase_and_next_gate(self):
        receipt = runtime.record_receipt(
            self.conn,
            "cloud",
            phase="Evidence runtime",
            action="Merged runtime receipt support",
            commit_sha="abc123",
            ci_status="success",
            next_gate="Deploy exact main",
            source="github-actions",
            evidence={"run_id": 42},
        )
        latest = runtime.latest_receipts(self.conn)["cloud"]
        project = {"id": "cloud", "phase": "stale", "next_step": "stale"}
        runtime.apply_receipt(project, latest)
        self.assertEqual("Evidence runtime", project["phase"])
        self.assertEqual("Deploy exact main", project["next_step"])
        self.assertEqual("evidence_receipt", project["state_source"])
        self.assertEqual(receipt["id"], project["execution_state"]["receipt_id"])

    def test_oversized_receipt_evidence_is_rejected_without_partial_row(self):
        with self.assertRaisesRegex(ValueError, "receipt evidence exceeds"):
            runtime.record_receipt(
                self.conn,
                "cloud",
                phase="Evidence runtime",
                evidence={"blob": "é" * 6001},
            )
        count = self.conn.execute("SELECT COUNT(*) FROM project_state_receipts").fetchone()[0]
        self.assertEqual(0, count)

    def test_malformed_stored_receipt_fails_closed_to_registry_state(self):
        self.conn.execute(
            """INSERT INTO project_state_receipts(
                project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "cloud",
                "untrusted phase",
                "untrusted action",
                "abc123",
                "success",
                "",
                "untrusted next gate",
                "corrupt-fixture",
                "2026-10-03T22:00:00+00:00",
                '{"broken":',
                "2026-10-03T22:00:00+00:00",
            ),
        )
        latest = runtime.latest_receipts(self.conn)["cloud"]
        self.assertIsNone(latest)
        project = {"id": "cloud", "phase": "registry phase", "next_step": "registry next"}
        runtime.apply_receipt(project, latest)
        self.assertEqual("registry phase", project["phase"])
        self.assertEqual("registry next", project["next_step"])
        self.assertEqual("registry", project["state_source"])

    def test_heavy_pool_allows_only_one_concurrent_project(self):
        first = runtime.acquire_resource(self.conn, "ftmo", "run-1")
        second = runtime.acquire_resource(self.conn, "haxlab", "run-2")
        self.assertTrue(first["acquired"])
        self.assertFalse(second["acquired"])
        self.assertEqual("resource_pool_busy", second["reason"])
        runtime.release_resource(self.conn, "ftmo", "run-1")
        third = runtime.acquire_resource(self.conn, "haxlab", "run-2")
        self.assertTrue(third["acquired"])

    def test_same_owner_renews_without_consuming_extra_slot(self):
        self.assertTrue(runtime.acquire_resource(self.conn, "ftmo", "run-1")["acquired"])
        renewed = runtime.acquire_resource(self.conn, "ftmo", "run-1")
        self.assertTrue(renewed["acquired"])
        self.assertTrue(renewed["renewed"])
        self.assertEqual(1, runtime.resource_status(self.conn)["pools"]["heavy"]["used"])

    def test_human_gated_project_cannot_acquire_compute(self):
        result = runtime.acquire_resource(self.conn, "ulab", "run-1")
        self.assertFalse(result["acquired"])
        self.assertEqual("resource_pool_disabled", result["reason"])

    def test_invalid_human_gate_contract_fails_closed(self):
        data = json.loads(self.contracts.read_text())
        data["projects"]["ulab"]["autonomy"]["auto_start"] = True
        self.contracts.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            runtime.load_contracts()


if __name__ == "__main__":
    unittest.main(verbosity=2)
