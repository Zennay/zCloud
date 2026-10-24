from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-portfolio-overview-proof.yml"


class PortfolioOverviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_exact_head(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.text)
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" ='), 2)
        self.assertIn("persist-credentials: false", self.text)

    def test_permanent_vps_identity_and_read_only_gets(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        for path in ("/api/runner-live", "/api/portfolio-queue?all=1", "/api/portfolio-attention", "/api/resources"):
            self.assertIn(path, self.text)
        self.assertIn("--require-complete", self.text)

    def test_workflow_has_no_mutation_routes_or_shell_writes(self):
        lowered = self.text.lower()
        for token in ("method=\"post\"", "sudo ", "systemctl ", "sqlite3 ", "git push", "/api/runner-control"):
            self.assertNotIn(token, lowered)


if __name__ == "__main__":
    unittest.main()
