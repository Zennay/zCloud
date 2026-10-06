from pathlib import Path
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "zcloud-notion-handoff-freshness-proof.yml"
)


class NotionHandoffFreshnessWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_exact_head(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_permanent_vps_proof_is_synthetic_only(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("mktemp -d", self.text)
        self.assertNotIn("curl ", self.text)
        self.assertNotIn("sqlite3 ", self.text)
        self.assertNotIn("notion_update", self.text.lower())
        self.assertNotIn("notion_create", self.text.lower())

    def test_contract_tests_run_on_hosted_before_vps(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("needs: validate", self.text)
        self.assertIn("tests.test_notion_handoff_freshness", self.text)
        self.assertIn("tests.test_notion_handoff_freshness_workflow", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
