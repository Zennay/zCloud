"""Trust-boundary contract for the zCloud telemetry-adapter proof workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-telemetry-adapters-proof.yml"


class ZcloudProjectTelemetryAdaptersProofWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_on_github_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  proof:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn(
            "tests.test_zcloud_project_telemetry_adapters_proof_workflow",
            validate,
        )

    def test_live_proof_is_manual_main_only_on_permanent_runner(self):
        proof = self.text.split("\n  proof:", 1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch'", proof)
        self.assertIn("github.repository == 'Zennay/zCloud'", proof)
        self.assertIn("github.ref == 'refs/heads/main'", proof)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", proof)

    def test_checkout_is_immutable_and_credentials_are_disabled(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertNotIn("actions/checkout@v5", self.text)

    def test_existing_exact_pr_head_contract_is_preserved(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  proof:", 1)[0]
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            validate,
        )
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            validate,
        )

    def test_live_runner_guard_precedes_repository_code(self):
        proof = self.text.split("\n  proof:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        trust_tests = "python3 -m unittest -v tests.test_zcloud_project_telemetry_adapters_proof_workflow"
        adapter_tests = "python3 -m unittest -v tests.test_evidence_progress"
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', proof)
        self.assertIn('test "$(id -un)" = "ubuntu"', proof)
        self.assertIn(guard, proof)
        self.assertLess(proof.index(guard), proof.index(trust_tests))
        self.assertLess(proof.index(guard), proof.index(adapter_tests))

    def test_adapter_validation_scope_is_preserved(self):
        for token in (
            "python3 -m py_compile enhancements.py tests/test_evidence_progress.py",
            "node --check public/enhancements.js",
            "python3 -m unittest -v tests.test_evidence_progress",
            "ZCLOUD_PROJECT_TELEMETRY_ADAPTERS_HOSTED_GREEN=1",
            "ZCLOUD_PROJECT_TELEMETRY_ADAPTERS_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_pull_request_job_contains_no_vps_identity_or_live_runner_guard(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  proof:", 1)[0]
        for token in (
            "zcloud_vps_runner_guard.py",
            'test "$(id -un)" = "ubuntu"',
            "runs-on: [self-hosted",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)


if __name__ == "__main__":
    unittest.main()
