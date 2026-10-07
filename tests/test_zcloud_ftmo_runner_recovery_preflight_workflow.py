from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-ftmo-runner-recovery-preflight.yml"


class FtmoRunnerRecoveryPreflightWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_permanent_zcloud_runner(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: [self-hosted, ftmo-research]", self.text)

    def test_pr_is_owner_and_same_repo_guarded(self):
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_exact_checkout_is_immutable_and_credential_free(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)

    def test_live_diagnostic_and_preflight_are_same_job(self):
        diagnostic = "python3 scripts/zcloud_ftmo_research_runner_diagnostic.py >"
        preflight = "python3 scripts/zcloud_ftmo_runner_recovery_preflight.py"
        same_job = "--same-job-live-evidence"
        self.assertIn(diagnostic, self.text)
        self.assertIn(preflight, self.text)
        self.assertIn(same_job, self.text)
        self.assertLess(self.text.index(diagnostic), self.text.index(preflight))

    def test_preflight_never_authorizes_mutation(self):
        self.assertIn('assert payload["mutation_authorized"] is False', self.text)

    def test_workflow_contains_no_runner_mutation(self):
        forbidden = (
            "systemctl start",
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "systemctl disable",
            "reset-failed",
            "kill ",
            "pkill ",
            "sudo ",
            "sqlite3 ",
            "git push",
            "gh api",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_evidence_is_boundedly_retained(self):
        self.assertIn("retention-days: 14", self.text)


if __name__ == "__main__":
    unittest.main()
