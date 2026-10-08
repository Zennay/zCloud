"""Contract tests for offline deploy-ops evidence admission. No external I/O."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "admission", Path(__file__).resolve().parents[1] / "scripts" / "deploy_ops_recovery_admission_offline.py"
)
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


def valid_bundle():
    data = {key: True for key in admission.REQUIRED_TRUE}
    data.update({
        "main_sha": "a" * 40, "candidate_sha": "a" * 40,
        "evidence_timestamp_utc": "2026-10-08T02:00:00Z",
        "reviewer": "release-owner", "trigger": "workflow_dispatch",
        "ref": "refs/heads/main", "dashboard_healthy": False,
    })
    return data


class OfflineRecoveryAdmissionTests(unittest.TestCase):
    def test_complete_unhealthy_exact_main_evidence(self):
        self.assertEqual(admission.evaluate(valid_bundle()), [])

    def test_pr_trigger_is_rejected(self):
        bundle = valid_bundle()
        bundle["trigger"] = "pull_request"
        self.assertIn("trigger_not_manual_dispatch", admission.evaluate(bundle))

    def test_healthy_dashboard_is_rejected(self):
        bundle = valid_bundle()
        bundle["dashboard_healthy"] = True
        self.assertIn("healthy_or_unknown_dashboard", admission.evaluate(bundle))

    def test_stale_sha_is_rejected(self):
        bundle = valid_bundle()
        bundle["candidate_sha"] = "b" * 40
        self.assertIn("stale_candidate_sha", admission.evaluate(bundle))

    def test_both_serialized_gates_are_required(self):
        for key in ("gate_580_released", "gate_1089_released"):
            with self.subTest(key=key):
                bundle = valid_bundle()
                bundle[key] = False
                self.assertIn("missing_or_false:" + key, admission.evaluate(bundle))

    def test_unknown_never_passes(self):
        self.assertTrue(admission.evaluate({}))
        self.assertTrue(admission.evaluate(None))
        bundle = valid_bundle()
        bundle.pop("rollback_ready")
        self.assertIn("missing_or_false:rollback_ready", admission.evaluate(bundle))

    def test_untrusted_ref_actor_runner_and_missing_proof(self):
        for key in ("canonical_repository", "trusted_actor", "permanent_runner_verified",
                    "pr_trigger_zero_mutation", "independent_outage_corroborated"):
            with self.subTest(key=key):
                bundle = valid_bundle()
                bundle[key] = None
                self.assertIn("missing_or_false:" + key, admission.evaluate(bundle))


if __name__ == "__main__":
    unittest.main()
