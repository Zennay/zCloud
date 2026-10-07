import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-service-restart-mapping.yml"


class ServiceRestartMappingWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_runs_for_main_prs_pushes_and_manual_dispatch(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("push:\n    branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("paths:", text)

    def test_is_hosted_read_only_and_exact_head(self):
        text = self.text()
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)

    def test_executes_only_isolated_process_restart_regression(self):
        text = self.text()
        self.assertIn(
            "python3 -m unittest -v tests.test_service_restart_mapping",
            text,
        )
        self.assertIn("timeout-minutes: 5", text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("ssh ", text)
        self.assertNotIn("/home/ubuntu", text)


if __name__ == "__main__":
    unittest.main()
