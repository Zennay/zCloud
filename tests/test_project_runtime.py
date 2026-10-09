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
                    "lane_profile": "research-validation",
                    "ai_worker_cap": 2,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"class": "research-heavy", "pool": "heavy", "cpu_soft_cores": 4, "memory_soft_mb": 4096},
                },
                "haxlab": {
                    "queue_mode": "execution",
                    "lane_profile": "ml-training",
                    "ai_worker_cap": 1,
                    "autonomy": {"mode": "ai_worker", "auto_start": True},
                    "compute": {"class": "ml-heavy", "pool": "heavy", "cpu_soft_cores": 1, "memory_soft_mb": 2048},
                },
                "ulab": {
                    "queue_mode": "human-gated",
                    "lane_profile": "human-gated",
                    "ai_worker_cap": 0,
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
        self.assertEqual(0, runtime.ai_worker_cap("ulab", 3))

    def test_zero_ai_worker_cap_requires_fail_closed_autonomy(self):
        data = json.loads(self.contracts.read_text())
        data["projects"]["cloud"]["ai_worker_cap"] = 0
        self.contracts.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "ai_worker_cap=0 requires fail-closed"):
            runtime.load_contracts()

    def test_unknown_lane_profile_fails_closed(self):
        data = json.loads(self.contracts.read_text())
        data["projects"]["cloud"]["lane_profile"] = "mystery"
        self.contracts.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "unsupported lane_profile"):
            runtime.load_contracts()

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

    def test_receipt_coverage_reports_missing_stale_and_current_projects(self):
        runtime.record_receipt(
            self.conn,
            "cloud",
            observed_at="2026-10-03T22:30:00+00:00",
            action="fresh cloud evidence",
            source="test",
        )
        runtime.record_receipt(
            self.conn,
            "ftmo",
            observed_at="2026-10-03T18:00:00+00:00",
            action="old ftmo evidence",
            source="test",
        )
        coverage = runtime.receipt_coverage(
            self.conn,
            ["cloud", "ftmo", "haxlab"],
            max_age_seconds=7200,
            now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertFalse(coverage["ready"])
        self.assertEqual(["cloud"], coverage["current"])
        self.assertEqual(["haxlab"], coverage["missing"])
        self.assertEqual("ftmo", coverage["stale"][0]["project_id"])
        self.assertEqual(3, coverage["project_count"])
        self.assertEqual(1, coverage["current_count"])

    def test_receipt_coverage_treats_malformed_latest_evidence_as_invalid(self):
        self.conn.execute(
            """INSERT INTO project_state_receipts(
                project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                observed_at,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "cloud", "phase", "action", "", "success", "", "", "test",
                "2026-10-03T22:30:00+00:00", '{"broken":', "2026-10-03T22:30:00+00:00",
            ),
        )
        coverage = runtime.receipt_coverage(
            self.conn,
            ["cloud"],
            max_age_seconds=7200,
            now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertFalse(coverage["ready"])
        self.assertEqual(["cloud"], coverage["invalid"])
        self.assertEqual([], coverage["missing"])

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

    def test_expired_heavy_lease_is_recovered_after_owner_crash(self):
        first = runtime.acquire_resource(self.conn, "ftmo", "crashed-owner")
        self.assertTrue(first["acquired"])
        self.conn.execute(
            "UPDATE resource_leases SET lease_until=? WHERE project_id=? AND owner_id=?",
            ("2000-01-01T00:00:00+00:00", "ftmo", "crashed-owner"),
        )

        recovered = runtime.acquire_resource(self.conn, "haxlab", "recovery-owner")
        self.assertTrue(recovered["acquired"])
        self.assertFalse(recovered["renewed"])

        rows = self.conn.execute(
            "SELECT project_id,owner_id FROM resource_leases ORDER BY project_id,owner_id"
        ).fetchall()
        self.assertEqual(
            [("haxlab", "recovery-owner")],
            [(row["project_id"], row["owner_id"]) for row in rows],
        )

    def test_heavy_contention_does_not_starve_protected_control_plane(self):
        heavy = runtime.acquire_resource(self.conn, "ftmo", "heavy-owner")
        blocked = runtime.acquire_resource(self.conn, "haxlab", "blocked-heavy-owner")
        protected = runtime.acquire_resource(self.conn, "cloud", "control-plane-owner")

        self.assertTrue(heavy["acquired"])
        self.assertFalse(blocked["acquired"])
        self.assertEqual("resource_pool_busy", blocked["reason"])
        self.assertTrue(protected["acquired"])

        status = runtime.resource_status(self.conn)
        self.assertEqual(1, status["pools"]["heavy"]["used"])
        self.assertEqual(1, status["pools"]["protected"]["used"])
        self.assertGreaterEqual(status["pools"]["protected"]["available"], 1)

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


    def test_boolean_resource_pool_slots_rejected_instead_of_cast_to_capacity(self):
        data = json.loads(self.contracts.read_text(encoding="utf-8"))
        for value in (True, False):
            with self.subTest(slots=value):
                data["resource_pools"]["protected"]["slots"] = value
                self.contracts.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "non-negative integer slots"):
                    runtime.load_contracts()
        data["resource_pools"]["protected"]["slots"] = 2
        self.contracts.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(2, runtime.load_contracts()["resource_pools"]["protected"]["slots"])

    def test_receipt_coverage_rejects_far_future_observation(self):
        runtime.record_receipt(
            self.conn, "cloud",
            observed_at="2026-10-04T00:00:00+00:00",
            action="incorrect clock", source="fixture",
        )
        coverage = runtime.receipt_coverage(
            self.conn, ["cloud"],
            max_age_seconds=7200,
            now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertFalse(coverage["ready"])
        self.assertEqual(["cloud"], coverage["invalid"])
        self.assertEqual([], coverage["current"])

    def test_receipt_coverage_rejects_naive_observed_timestamp(self):
        runtime.record_receipt(
            self.conn, "cloud",
            observed_at="2026-10-03T22:30:00",
            action="ambiguous local clock", source="fixture",
        )
        coverage = runtime.receipt_coverage(
            self.conn, ["cloud"], now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertFalse(coverage["ready"])
        self.assertEqual(["cloud"], coverage["invalid"])

    def test_receipt_coverage_rejects_naive_reference_timestamp(self):
        with self.assertRaisesRegex(ValueError, "invalid receipt coverage reference time"):
            runtime.receipt_coverage(self.conn, ["cloud"], now_value="2026-10-03T23:00:00")

    def test_receipt_coverage_accepts_timezone_offset_observation(self):
        runtime.record_receipt(
            self.conn, "cloud",
            observed_at="2026-10-04T00:30:00+02:00",
            action="offset-aware timestamp", source="fixture",
        )
        coverage = runtime.receipt_coverage(
            self.conn, ["cloud"], max_age_seconds=7200,
            now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertTrue(coverage["ready"])
        self.assertEqual(["cloud"], coverage["current"])

    def test_receipt_coverage_has_bounded_future_clock_skew(self):
        reference = "2026-10-03T23:00:00+00:00"
        for value, is_valid in (
            ("2026-10-03T23:01:00+00:00", True),
            ("2026-10-03T23:01:01+00:00", False),
        ):
            with self.subTest(observed_at=value):
                self.conn.execute("DELETE FROM project_state_receipts")
                runtime.record_receipt(
                    self.conn, "cloud", observed_at=value,
                    action="clock skew threshold", source="fixture",
                )
                coverage = runtime.receipt_coverage(
                    self.conn, ["cloud"], now_value=reference,
                )
                self.assertEqual(is_valid, coverage["ready"])
                self.assertEqual([] if is_valid else ["cloud"], coverage["invalid"])


    def test_explicit_zero_receipt_window_does_not_restore_24_hour_default(self):
        runtime.record_receipt(
            self.conn, "cloud",
            observed_at="2026-10-03T22:58:30+00:00",
            source="fixture",
        )
        coverage = runtime.receipt_coverage(
            self.conn, ["cloud"], max_age_seconds=0,
            now_value="2026-10-03T23:00:00+00:00",
        )
        self.assertEqual(60, coverage["max_age_seconds"])
        self.assertFalse(coverage["ready"])
        self.assertEqual(["cloud"], [item["project_id"] for item in coverage["stale"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
