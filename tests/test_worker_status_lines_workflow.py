from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-status-lines-proof.yml"


class WorkerStatusLineWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_read_only_exact_head_contract(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.text)
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" ='), 2)
        self.assertIn("persist-credentials: false", self.text)

    def test_permanent_vps_uses_only_runner_live_get(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("/api/runner-live", self.text)
        self.assertIn("--require-complete", self.text)

    def test_no_mutation_route_or_shell_write(self):
        lowered = self.text.lower()
        for token in ("method=\"post\"", "/api/runner-control", "sudo ", "systemctl ", "sqlite3 ", "git push"):
            self.assertNotIn(token, lowered)


if __name__ == "__main__":
    unittest.main()
