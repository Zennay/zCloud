"""Regression tests for the deploy receipt drift comparator."""
import copy
import unittest
from scripts.zcloud_deploy_receipt_drift_compare import compare

BASE = {
    "candidate_sha": "a" * 40,
    "deployed_sha": "a" * 40,
    "main_sha": "b" * 40,
    "workflow_run_id": 123,
    "runner_name": "zcloud-vps-1",
}

class ReceiptDriftTests(unittest.TestCase):
    def test_identical_is_non_authorizing(self):
        result = compare(BASE, copy.deepcopy(BASE))
        self.assertFalse(result["evidence_drift_detected"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_every_identity_field_detects_drift(self):
        replacements = {"candidate_sha": "c" * 40, "deployed_sha": "c" * 40,
                        "main_sha": "d" * 40, "workflow_run_id": 124,
                        "runner_name": "another-runner"}
        for field, replacement in replacements.items():
            with self.subTest(field=field):
                altered = {**BASE, field: replacement}
                result = compare(BASE, altered)
                self.assertEqual(result["changed_fields"], [field])
                self.assertTrue(result["evidence_drift_detected"])
                self.assertFalse(result["deploy_authorized"])

    def test_missing_or_malformed_fails_closed(self):
        for field in BASE:
            with self.subTest(field=field):
                altered = dict(BASE)
                del altered[field]
                with self.assertRaises(ValueError):
                    compare(BASE, altered)
        for bad in (None, [], "text"):
            with self.assertRaises(ValueError):
                compare(BASE, bad)
        with self.assertRaises(ValueError):
            compare(BASE, {**BASE, "candidate_sha": "not-a-sha"})
        with self.assertRaises(ValueError):
            compare(BASE, {**BASE, "workflow_run_id": True})

if __name__ == "__main__":
    unittest.main()
