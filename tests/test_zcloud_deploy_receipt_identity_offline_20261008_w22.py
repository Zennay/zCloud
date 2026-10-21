"""Negative-only, offline tests for receipt identity; no deployment permission."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_receipt_identity_offline_20261008_w22.py"
spec = importlib.util.spec_from_file_location("receipt_contract", MODULE)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
EXPECTED = dict(repository="Zennay/zCloud", workflow_run_id=123,
                run_attempt=2, head_sha=SHA, environment="production",
                artifact_digest=DIGEST, deployment_id=456)


class ReceiptIdentityTests(unittest.TestCase):
    def test_matching_evidence_is_not_authorization(self):
        self.assertTrue(contract.receipt_matches(EXPECTED.copy(), **EXPECTED))
        self.assertFalse(contract.live_deployment_authorized(EXPECTED))

    def test_missing_and_extra_fields_denied(self):
        for changed in (dict(EXPECTED, unexpected="x"),
                        {k:v for k,v in EXPECTED.items() if k != "deployment_id"}):
            self.assertFalse(contract.receipt_matches(changed, **EXPECTED))

    def test_missing_and_wrong_shapes_denied(self):
        for changed in (None, [], "receipt", 42, {}, {"x": "y"}):
            self.assertFalse(contract.receipt_matches(changed, **EXPECTED))

    def test_replayed_attempt_denied(self):
        self.assertFalse(contract.receipt_matches(dict(EXPECTED, run_attempt=1), **EXPECTED))

    def test_replayed_run_denied(self):
        self.assertFalse(contract.receipt_matches(dict(EXPECTED, workflow_run_id=122), **EXPECTED))

    def test_cross_environment_and_repository_denied(self):
        for key, value in (("environment", "staging"), ("repository", "other/zCloud")):
            self.assertFalse(contract.receipt_matches(dict(EXPECTED, **{key:value}), **EXPECTED))

    def test_mismatched_artifact_and_deployment_denied(self):
        for key, value in (("artifact_digest", "sha256:" + "c"*64),
                           ("deployment_id", 457)):
            self.assertFalse(contract.receipt_matches(dict(EXPECTED, **{key:value}), **EXPECTED))

    def test_invalid_sha_and_digest_denied(self):
        for key, value in (("head_sha", "A"*40), ("head_sha", "abc"),
                           ("artifact_digest", "sha1:"+"b"*64),
                           ("artifact_digest", "sha256:"+"B"*64)):
            self.assertFalse(contract.receipt_matches(dict(EXPECTED, **{key:value}), **EXPECTED))

    def test_boolean_and_numeric_string_denied(self):
        for key, value in (("workflow_run_id", True), ("run_attempt", "2"),
                           ("deployment_id", 0)):
            self.assertFalse(contract.receipt_matches(dict(EXPECTED, **{key:value}), **EXPECTED))

    def test_invalid_expectations_denied(self):
        for key, value in (("environment", ""), ("head_sha", "invalid"),
                           ("repository", "../zCloud"), ("run_attempt", True)):
            self.assertFalse(contract.receipt_matches(EXPECTED, **dict(EXPECTED, **{key:value})))


if __name__ == "__main__":
    unittest.main()
