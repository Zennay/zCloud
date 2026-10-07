import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-migration-safety.yml"


class MigrationSafetyWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_sha_immutable_checkout_and_read_only_permissions(self):
        self.assertNotIn("\\${{", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        self.assertGreaterEqual(self.text.count("fetch-depth: 0"), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_self_hosted_job_is_owner_same_repo_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_guard_uses_exact_pr_base_and_head(self):
        self.assertIn("github.event.pull_request.base.sha", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn('zcloud_migration_safety_guard.py --base "$BASE_SHA" --head "$HEAD_SHA"', self.text)

    def test_workflow_contains_no_production_mutation_commands(self):
        for token in (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X POST",
            "git push",
            "gh pr merge",
        ):
            self.assertNotIn(token, self.text)

    def test_relevant_config_and_migration_paths_trigger_guard(self):
        for path in (
            "server.py",
            "project_runtime.py",
            "project-contracts.json",
            "config-schema-versions.json",
            "docs/migrations/**",
            "scripts/**",
        ):
            self.assertIn(path, self.text)


if __name__ == "__main__":
    unittest.main()
