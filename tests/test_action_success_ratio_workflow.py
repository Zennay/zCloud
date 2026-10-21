from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-action-success-ratio-proof.yml"


class ActionSuccessRatioWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_head_read_only_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.text)
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" ='), 2)
        self.assertIn("persist-credentials: false", self.text)

    def test_permanent_vps_reads_live_database_without_mutation(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("/home/ubuntu/zennay-cloud/history.db", self.text)
        self.assertIn("--hours 168 --require-complete", self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_no_live_mutation_commands(self):
        lowered = self.text.lower()
        for token in ("sudo ", "systemctl ", "sqlite3 ", "git push", "method=\"post\""):
            self.assertNotIn(token, lowered)


if __name__ == "__main__":
    unittest.main()
