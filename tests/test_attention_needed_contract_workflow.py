import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-attention-needed-contract.yml"


class AttentionNeededWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_head_and_read_only_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_permanent_vps_job_is_same_repo_owner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("ZCLOUD_ATTENTION_NEEDED_VPS_GREEN=1", self.text)

    def test_proof_does_not_mutate_production_services_or_database(self):
        for token in (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X POST",
            "/home/ubuntu/zennay-cloud/history.db",
        ):
            self.assertNotIn(token, self.text)

    def test_real_implementation_paths_trigger_contract(self):
        self.assertIn("'server.py'", self.text)
        self.assertIn("'public/app.js'", self.text)


if __name__ == "__main__":
    unittest.main()
