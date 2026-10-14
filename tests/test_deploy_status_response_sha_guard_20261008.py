"""Offline regression for exact-SHA deployment status proof."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "deploy_status_response_sha_guard",
    ROOT / "scripts" / "deploy_status_response_sha_guard_20261008.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
GOOD = "a" * 40
OTHER = "b" * 40


class StatusShaGuardTests(unittest.TestCase):
    def test_exact_sha_accepted_without_granting_deploy_authority(self):
        proof = {"sha": GOOD, "statuses": [], "state": "success"}
        self.assertIs(module.validate_status_response(GOOD, proof), proof)

    def test_rejects_missing_sha(self):
        with self.assertRaisesRegex(module.StatusProofRejected, "invalid_response_sha"):
            module.validate_status_response(GOOD, {"statuses": []})

    def test_rejects_stale_sha(self):
        with self.assertRaisesRegex(module.StatusProofRejected, "response_sha_mismatch"):
            module.validate_status_response(GOOD, {"sha": OTHER, "statuses": []})

    def test_rejects_symbolic_or_uppercase_sha(self):
        for sha in ("main", GOOD.upper(), "a" * 39, "a" * 41):
            with self.subTest(sha=sha), self.assertRaises(module.StatusProofRejected):
                module.validate_status_response(GOOD, {"sha": sha, "statuses": []})

    def test_rejects_untrusted_candidate(self):
        for sha in (None, "main", GOOD.upper(), "f" * 39):
            with self.subTest(sha=sha), self.assertRaisesRegex(
                module.StatusProofRejected, "invalid_candidate_sha"
            ):
                module.validate_status_response(sha, {"sha": GOOD, "statuses": []})

    def test_rejects_wrong_payload_shapes(self):
        for payload in (None, [], "unavailable", {"sha": GOOD}, {"sha": GOOD, "statuses": {}}):
            with self.subTest(payload=payload), self.assertRaises(module.StatusProofRejected):
                module.validate_status_response(GOOD, payload)


if __name__ == "__main__":
    unittest.main()
