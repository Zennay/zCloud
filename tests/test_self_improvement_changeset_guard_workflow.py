import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-improvement-changeset.yml"


class SelfImprovementChangeSetWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_prs_and_main_pushes_with_read_only_permissions(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("push:", self.text)
        self.assertGreaterEqual(self.text.count("branches: [main]"), 2)
        self.assertIn("contents: read", self.text)
        self.assertIn("pull-requests: read", self.text)

    def test_exact_sha_immutable_checkout_everywhere(self):
        pin = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertGreaterEqual(self.text.count(pin), 3)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 3)
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'), 3)
        self.assertNotIn("actions/checkout@v", self.text)

    def test_permanent_vps_proof_is_owner_same_repo_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_push_provenance_uses_exact_commit_pull_association(self):
        self.assertIn("/commits/\${GITHUB_SHA}/pulls", self.text)
        self.assertIn("associated-prs.json", self.text)
        self.assertIn("zcloud_self_improvement_changeset_guard.py push", self.text)
        self.assertIn('--ref "\${GITHUB_REF}"', self.text)

    def test_pr_guard_receives_base_head_and_branch_refs(self):
        for token in (
            "github.event.pull_request.base.sha",
            "github.event.pull_request.head.sha",
            "github.event.pull_request.base.ref",
            "github.event.pull_request.head.ref",
            "zcloud_self_improvement_changeset_guard.py pr",
        ):
            self.assertIn(token, self.text)

    def test_workflow_contains_no_production_mutation_commands(self):
        for token in (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X",
            "git push",
            "gh pr merge",
            "gh pr close",
        ):
            self.assertNotIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
