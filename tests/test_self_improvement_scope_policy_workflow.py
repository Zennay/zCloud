from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-improvement-scope-policy.yml"


class SelfImprovementScopePolicyWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_and_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_workflow_has_no_runtime_or_repo_mutation_surface(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in (
            "contents: write",
            "actions: write",
            "statuses: write",
            "pull-requests: write",
            "systemctl",
            "sudo ",
            "history.db",
            "sqlite3",
            "gh api",
            "curl ",
            "server.py",
            "portfolio_queue",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
