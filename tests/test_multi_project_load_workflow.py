import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-multiproject-load.yml"


class MultiProjectLoadWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_load_gate_runs_for_every_main_pr_and_main_push(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("push:\n    branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("paths:", text)

    def test_load_gate_is_hosted_read_only_and_exact_candidate(self):
        text = self.text()
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("timeout-minutes: 5", text)

    def test_load_gate_executes_only_bounded_synthetic_suite(self):
        text = self.text()
        self.assertIn(
            "python3 -m unittest -v tests.test_multi_project_load",
            text,
        )
        self.assertNotIn("systemctl", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("ssh ", text)
        self.assertNotIn("/home/ubuntu", text)


if __name__ == "__main__":
    unittest.main()
