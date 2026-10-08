"""Isolated regression tests: no GitHub API or live deployment side effects."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_evidence_boundary.py"
spec = importlib.util.spec_from_file_location("deploy_evidence_boundary", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
A = "a" * 40
B = "b" * 40


def fixture():
    return {
        "main_sha": A, "candidate_base_sha": A, "post_ci_main_sha": A,
        "candidate_head_sha": B, "validated_head_sha": B,
        "deploy_authorized": False, "merge_authorized": False,
        "mutation_performed": False,
        "checks": {key: {"conclusion": "success", "head_sha": B, "run_id": 100 + i}
                   for i, key in enumerate(module.CHECKS)},
    }


class EvidenceBoundaryTests(unittest.TestCase):
    def test_consistent_evidence_is_still_not_release_authority(self):
        self.assertEqual(module.assess(fixture()), [])

    def test_post_ci_main_drift_fails_closed(self):
        data = fixture()
        data["post_ci_main_sha"] = B
        self.assertIn("main_drift", module.assess(data))

    def test_outdated_candidate_base_fails_closed(self):
        data = fixture()
        data["candidate_base_sha"] = B
        self.assertIn("candidate_base_mismatch", module.assess(data))

    def test_old_head_proof_fails_closed(self):
        data = fixture()
        data["checks"]["regression"]["head_sha"] = A
        self.assertIn("invalid_check_regression", module.assess(data))

    def test_missing_check_fails_closed(self):
        data = fixture()
        del data["checks"]["immutable_intent"]
        self.assertIn("invalid_check_immutable_intent", module.assess(data))

    def test_workflow_success_without_positive_run_id_rejected(self):
        data = fixture()
        data["checks"]["cpu"]["run_id"] = 0
        self.assertIn("invalid_check_cpu", module.assess(data))

    def test_true_authorization_flags_rejected(self):
        data = fixture()
        data["deploy_authorized"] = True
        data["merge_authorized"] = True
        data["mutation_performed"] = True
        self.assertEqual(set(module.assess(data)),
                         {"non_authorizing_envelope_required",
                          "merge_not_authorized", "mutation_flag_invalid"})

    def test_missing_sha_and_non_object_fail_closed(self):
        data = fixture()
        data["main_sha"] = "main"
        self.assertIn("invalid_main_sha", module.assess(data))
        self.assertEqual(module.assess(None), ["invalid_payload"])


if __name__ == "__main__":
    unittest.main()
