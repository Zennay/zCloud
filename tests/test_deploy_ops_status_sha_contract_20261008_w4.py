"""Offline regression fixtures for #1160; no VPS or GitHub calls."""
import importlib.util
from pathlib import Path
import unittest

module_path = Path(__file__).resolve().parents[1] / "tools" / "deploy_ops_status_sha_contract_20261008_w4.py"
spec = importlib.util.spec_from_file_location("status_sha_contract", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
verify = module.verify_production_status
Error = module.StatusEvidenceError

A = "a" * 40
B = "b" * 40


def payload(sha=A, statuses=None):
    return {"sha": sha, "statuses": [{"context": "zcloud/vps-production", "state": "success"}] if statuses is None else statuses}


class ShaBindingContract(unittest.TestCase):
    def test_stable_green_exact_head(self):
        self.assertTrue(verify(A, A, A, payload()))

    def test_stable_non_green_is_not_already_green(self):
        self.assertFalse(verify(A, A, A, payload(statuses=[{"context": "zcloud/vps-production", "state": "failure"}])))

    def test_other_context_cannot_supply_green(self):
        self.assertFalse(verify(A, A, A, payload(statuses=[{"context": "other", "state": "success"}])))

    def test_latest_context_wins(self):
        self.assertFalse(verify(A, A, A, payload(statuses=[
            {"context": "zcloud/vps-production", "state": "pending"},
            {"context": "zcloud/vps-production", "state": "success"},
        ])))

    def test_main_moves_between_checks(self):
        with self.assertRaisesRegex(Error, "drift"):
            verify(A, A, B, payload())

    def test_candidate_is_not_preflight_main(self):
        with self.assertRaisesRegex(Error, "mismatch"):
            verify(A, B, B, payload(sha=B))

    def test_stale_green_response_rejected(self):
        with self.assertRaisesRegex(Error, "response SHA mismatch"):
            verify(A, A, A, payload(sha=B))

    def test_missing_response_sha_rejected(self):
        with self.assertRaises(Error):
            verify(A, A, A, {"statuses": []})

    def test_bad_response_sha_rejected(self):
        with self.assertRaises(Error):
            verify(A, A, A, payload(sha="main"))

    def test_malformed_status_list_rejected(self):
        with self.assertRaises(Error):
            verify(A, A, A, {"sha": A, "statuses": {}})

    def test_malformed_status_entry_rejected(self):
        with self.assertRaises(Error):
            verify(A, A, A, payload(statuses=[{"context": "zcloud/vps-production"}]))

    def test_bad_candidate_sha_rejected(self):
        with self.assertRaises(Error):
            verify("refs/heads/main", A, A, payload())

    def test_api_error_representation_rejected(self):
        with self.assertRaises(Error):
            verify(A, A, A, {"message": "API rate limit exceeded"})


if __name__ == "__main__":
    unittest.main()
