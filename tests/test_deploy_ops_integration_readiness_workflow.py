import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-deploy-ops-integration-readiness.yml"


class DeployOpsIntegrationReadinessWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_hosted_read_only(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotIn("self-hosted", self.text)
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read\n  actions: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_checkout_is_immutable_exact_revision_without_credentials(self):
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, flags=re.MULTILINE)
        self.assertEqual(
            uses,
            ["actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"],
        )
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_live_step_is_inventory_only(self):
        inventory = self.text.split(
            "- name: Inventory open deploy-ops PR integration readiness", 1
        )[1]
        self.assertIn("zcloud_deploy_ops_integration_readiness.py", inventory)
        self.assertIn("--format markdown", inventory)
        for forbidden in (
            "git push",
            "gh pr merge",
            "gh pr update-branch",
            "systemctl",
            "sudo ",
            "ssh ",
            "/home/ubuntu/",
        ):
            self.assertNotIn(forbidden, inventory)

    def test_focused_tests_are_run_before_live_inventory(self):
        tests_at = self.text.index("Run focused readiness regressions")
        live_at = self.text.index("Inventory open deploy-ops PR integration readiness")
        self.assertLess(tests_at, live_at)
        self.assertIn("tests.test_deploy_ops_integration_readiness", self.text)
        self.assertIn("tests.test_deploy_ops_integration_readiness_workflow", self.text)


if __name__ == "__main__":
    unittest.main()
