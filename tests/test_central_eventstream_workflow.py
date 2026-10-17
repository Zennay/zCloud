from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-central-eventstream-proof.yml"


class CentralEventStreamWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_read_only_permissions_and_immutable_checkout(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_exact_head_and_permanent_runner_guard_are_required(self):
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" ='), 2)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_live_smoke_is_read_only_bounded_and_fail_closed(self):
        self.assertIn("/home/ubuntu/zennay-cloud/history.db", self.text)
        self.assertIn("--project cloud --limit 20 --require-complete", self.text)
        self.assertIn("ZCLOUD_CENTRAL_EVENTSTREAM_LIVE_READ_GREEN", self.text)
        self.assertNotIn("cat $out", self.text)
        self.assertNotIn('cat "$out"', self.text)

    def test_workflow_has_no_live_mutation_commands(self):
        lowered = self.text.lower()
        for token in ("sudo ", "systemctl ", "sqlite3 ", "curl -x post", "git push"):
            self.assertNotIn(token, lowered)


if __name__ == "__main__":
    unittest.main()
