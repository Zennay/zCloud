import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-dead-conversation-cleanup-selector-proof.yml"


class DeadConversationCleanupSelectorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_exact_head_bound(self):
        text = self.text
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn('test "$(git rev-parse HEAD)" =', text)

    def test_permanent_vps_path_is_owner_same_repo_guarded(self):
        text = self.text
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)

    def test_workflow_has_no_live_mutation_commands(self):
        lowered = self.text.lower()
        forbidden = (
            "sudo ",
            "systemctl ",
            "git push",
            "gh api ",
            "gh pr ",
            "curl -x ",
            "curl --request",
            "rm -",
            "unlink ",
            "sqlite3 ",
            "delete from ",
            "update runner_",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, lowered)

    def test_dedicated_jobs_run_both_contract_suites(self):
        text = self.text
        command = (
            "python3 -m unittest -v "
            "tests.test_dead_conversation_cleanup_selector "
            "tests.test_dead_conversation_cleanup_selector_workflow"
        )
        self.assertEqual(2, text.count(command))


if __name__ == "__main__":
    unittest.main()
