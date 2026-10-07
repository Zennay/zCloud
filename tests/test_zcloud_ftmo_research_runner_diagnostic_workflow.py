from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-ftmo-research-runner-diagnostic.yml"


class FtmoResearchRunnerDiagnosticWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_permanent_zcloud_runner_not_ftmo_runner(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: [self-hosted, ftmo-research]", self.text)

    def test_pr_is_owner_and_same_repo_guarded(self):
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_checkout_is_exact_immutable_and_credential_free(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)

    def test_runner_guard_precedes_diagnostic(self):
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        diagnostic = "python3 scripts/zcloud_ftmo_research_runner_diagnostic.py | tee"
        self.assertIn(guard, self.text)
        self.assertIn(diagnostic, self.text)
        self.assertLess(self.text.index(guard), self.text.index(diagnostic))

    def test_workflow_is_observation_only(self):
        forbidden = (
            "systemctl restart",
            "systemctl start",
            "systemctl stop",
            "systemctl enable",
            "systemctl disable",
            "sqlite3 ",
            "git push",
            "sudo ",
            "gh api",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_evidence_is_retained_boundedly(self):
        self.assertIn("retention-days: 14", self.text)
        self.assertIn("mutation_performed", self.text)


if __name__ == "__main__":
    unittest.main()
