"""Regression tests for the read-only post-deploy receipt validator."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "receipt_check", ROOT / "scripts" / "zcloud_postdeploy_receipt_offline_check.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

S = "a" * 40
def good():
    return {
        "main_sha": "b" * 40, "candidate_sha": S, "deployed_sha": S,
        "serialized_gate_released": True, "runner_identity_verified": True,
        "regression_green": True, "prewrite_guard_passed": True,
        "external_health_verified": True, "rollback_required": False,
        "mutation_authorized": False,
        "runner_proof_url": "https://github.com/example/runs/1",
        "regression_url": "https://github.com/example/runs/2",
        "health_receipt_url": "https://example.org/health/receipt",
    }

class ReceiptCheckTests(unittest.TestCase):
    def test_complete_receipt_is_syntactically_valid_not_authorization(self):
        self.assertEqual(MOD.validate(good()), [])

    def test_missing_or_wrong_flags_are_rejected(self):
        for key in MOD.REQUIRED_TRUE:
            for value in (None, False, "true", 1):
                with self.subTest(key=key, value=value):
                    receipt = good()
                    receipt[key] = value
                    self.assertTrue(MOD.validate(receipt))
        for key in MOD.REQUIRED_FALSE:
            for value in (None, True, "false", 0):
                with self.subTest(key=key, value=value):
                    receipt = good()
                    receipt[key] = value
                    self.assertTrue(MOD.validate(receipt))

    def test_sha_mismatch_and_invalid_format(self):
        receipt = good()
        receipt["deployed_sha"] = "c" * 40
        self.assertIn("deployed_sha_mismatch", MOD.validate(receipt))
        receipt["candidate_sha"] = "not-a-sha"
        self.assertIn("candidate_sha_invalid", MOD.validate(receipt))

    def test_receipt_links_must_be_https(self):
        for field in ("runner_proof_url", "regression_url", "health_receipt_url"):
            receipt = good()
            receipt[field] = "http://example.invalid/receipt"
            self.assertIn(f"{field}_missing_https", MOD.validate(receipt))

    def test_malformed_or_missing_fields_fail_closed(self):
        self.assertEqual(MOD.validate([]), ["receipt_must_be_object"])
        self.assertTrue(MOD.validate({}))
        self.assertTrue(MOD.validate(None))

if __name__ == "__main__":
    unittest.main()
