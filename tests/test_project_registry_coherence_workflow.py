import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-registry-coherence.yml"


class ProjectRegistryCoherenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_proof_is_read_only_and_uses_permanent_vps(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("systemctl ", self.text)
        self.assertNotIn("sudo ", self.text)

    def test_pull_request_proof_checks_out_exact_head_without_persisted_credentials(self):
        expression = (
            "EXPECTED_SHA: ${{ github.event_name == 'pull_request' "
            "&& github.event.pull_request.head.sha || github.sha }}"
        )
        self.assertEqual(2, self.text.count(expression))
        self.assertEqual(2, self.text.count("ref: ${{ env.EXPECTED_SHA }}"))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertEqual(
            2,
            self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'),
        )

    def test_checkout_is_immutable_and_shared_by_both_proof_jobs(self):
        checkout = "uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(checkout))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertNotIn("actions/checkout@v5", self.text)
        self.assertNotIn("actions/checkout@v6", self.text)

    def test_superseded_proofs_are_concurrency_bounded(self):
        self.assertIn(
            "group: zcloud-project-registry-coherence-${{ github.event.pull_request.number || github.ref }}",
            self.text,
        )
        self.assertIn("cancel-in-progress: true", self.text)


if __name__ == "__main__":
    unittest.main()
