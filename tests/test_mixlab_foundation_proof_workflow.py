"""Trust-boundary contract for the MixLab foundation proof workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "mixlab-foundation-proof.yml"


class MixlabFoundationProofWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_on_github_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  proof:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_mixlab_foundation_proof_workflow", validate)
        self.assertNotIn("/api/portfolio-queue", validate)
        self.assertNotIn("Zennay/Mixlab", validate)

    def test_live_proof_is_trusted_main_only_on_permanent_runner(self):
        proof = self.text.split("\n  proof:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", proof)
        self.assertIn("github.repository == 'Zennay/zCloud'", proof)
        self.assertIn("github.ref == 'refs/heads/main'", proof)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", proof)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_checkouts_are_pinned_exact_and_credentials_disabled(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(3, self.text.count(pinned))
        self.assertEqual(3, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertIn("EXPECTED_ZCLOUD_SHA: ${{ github.event.pull_request.head.sha }}", self.text)
        self.assertIn("EXPECTED_ZCLOUD_SHA: ${{ github.sha }}", self.text)

    def test_runner_guard_precedes_external_candidate_and_queue_write(self):
        proof = self.text.split("\n  proof:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        external_checkout = "name: Checkout exact MixLab foundation"
        queue_write = 'urllib.request.Request("http://127.0.0.1:8765/api/portfolio-queue"'
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', proof)
        self.assertIn('test "$(id -un)" = "ubuntu"', proof)
        self.assertIn(guard, proof)
        self.assertLess(proof.index(guard), proof.index(external_checkout))
        self.assertLess(proof.index(guard), proof.index(queue_write))

    def test_existing_mixlab_proof_and_handoff_semantics_are_preserved(self):
        for token in (
            "MIXLAB_SHA: df182461432e7f6e983f72614d850ce7daec0088",
            "repository: Zennay/Mixlab",
            "python3 ops/prove.py",
            '"queue_id": "zssh-mixlab-foundation-integration-20261002"',
            '"project_id": "zssh", "priority": "P2"',
            'result.get("backend") != "sqlite"',
            "queue-receipt.json",
            "retention-days: 14",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_pr_trigger_is_limited_to_workflow_and_contract_test(self):
        trigger = self.text.split("  push:", 1)[0]
        self.assertIn('".github/workflows/mixlab-foundation-proof.yml"', trigger)
        self.assertIn('"tests/test_mixlab_foundation_proof_workflow.py"', trigger)


if __name__ == "__main__":
    unittest.main()
