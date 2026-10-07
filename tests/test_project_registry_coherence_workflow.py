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


if __name__ == "__main__":
    unittest.main()
