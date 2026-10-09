"""Regression tests for strict, UTC-aware project receipt and pool admission.

Synthetic receipts live exclusively in an in-memory SQLite database.
No runner, VPS, live queue, or deployed configuration is modified.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_runtime as runtime


class ProjectRuntimeEvidenceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        runtime.init_tables(self.db)

    def tearDown(self):
        self.db.close()

    def receipt(self, observed_at):
        return runtime.record_receipt(
            self.db, "cloud", observed_at=observed_at,
            phase="validation", source="synthetic-test",
            evidence={"test": "no-live-side-effects"},
        )

    def coverage(self, reference="2026-10-09T16:00:00+00:00", max_age=120):
        return runtime.receipt_coverage(
            self.db, ["cloud"], now_value=reference,
            max_age_seconds=max_age,
        )

    def test_future_utc_receipt_cannot_authorize_freshness(self):
        self.receipt("2026-10-09T16:00:01+00:00")
        result = self.coverage()
        self.assertFalse(result["ready"])
        self.assertEqual(["cloud"], result["invalid"])
        self.assertEqual([], result["current"])
        self.assertEqual(0, result["current_count"])

    def test_future_offset_receipt_cannot_authorize_freshness(self):
        self.receipt("2026-10-09T17:00:01+01:00")
        result = self.coverage()
        self.assertFalse(result["ready"])
        self.assertEqual(["cloud"], result["invalid"])

    def test_naive_receipt_is_invalid_not_assumed_local_timezone(self):
        self.receipt("2026-10-09T15:59:59")
        result = self.coverage()
        self.assertFalse(result["ready"])
        self.assertEqual(["cloud"], result["invalid"])

    def test_naive_reference_rejected_instead_of_host_timezone_fallback(self):
        self.receipt("2026-10-09T15:59:59+00:00")
        with self.assertRaisesRegex(ValueError, "invalid receipt coverage reference time"):
            self.coverage(reference="2026-10-09T16:00:00")

    def test_aware_offset_receipt_at_exact_max_age_is_current(self):
        self.receipt("2026-10-09T16:58:00+01:00")
        result = self.coverage()
        self.assertTrue(result["ready"])
        self.assertEqual(["cloud"], result["current"])
        self.assertEqual([], result["stale"])

    def test_aware_offset_receipt_past_max_age_is_stale(self):
        self.receipt("2026-10-09T16:57:59+01:00")
        result = self.coverage()
        self.assertFalse(result["ready"])
        self.assertEqual("cloud", result["stale"][0]["project_id"])

    def test_subsecond_expiry_beyond_boundary_is_stale(self):
        self.receipt("2026-10-09T15:57:59.999999+00:00")
        result = self.coverage()
        self.assertFalse(result["ready"])
        self.assertEqual("cloud", result["stale"][0]["project_id"])
        self.assertGreater(result["stale"][0]["age_seconds"], 120)

    def test_exact_reference_time_is_current(self):
        self.receipt("2026-10-09T16:00:00Z")
        self.assertTrue(self.coverage()["ready"])

    def test_boolean_pool_capacity_is_not_a_slot_count(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-boolean-pool-") as temp:
            contracts = json.loads(runtime.CONTRACT_FILE.read_text(encoding="utf-8"))
            pool_name = next(iter(contracts["resource_pools"]))
            contracts["resource_pools"][pool_name]["slots"] = True
            target = Path(temp) / "contracts.json"
            target.write_text(json.dumps(contracts), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integer slots"):
                runtime.load_contracts(target)

    def test_numeric_pool_capacity_still_accepted(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-numeric-pool-") as temp:
            contracts = json.loads(runtime.CONTRACT_FILE.read_text(encoding="utf-8"))
            pool_name = next(iter(contracts["resource_pools"]))
            contracts["resource_pools"][pool_name]["slots"] = 2
            target = Path(temp) / "contracts.json"
            target.write_text(json.dumps(contracts), encoding="utf-8")
            self.assertEqual(2, runtime.load_contracts(target)["resource_pools"][pool_name]["slots"])


if __name__ == "__main__":
    unittest.main()
