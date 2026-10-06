from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-action-outcome-report.yml"
PINNED_CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


class ActionOutcomeWorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_permanent_vps_and_least_privilege_contract(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_checkout_is_immutable_exact_head_without_credentials(self):
        self.assertIn(PINNED_CHECKOUT, self.text)
        self.assertNotIn("actions/checkout@v", self.text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_live_proof_is_read_only_and_bounded(self):
        self.assertIn("timeout-minutes: 5", self.text)
        self.assertIn(
            "python3 -m unittest tests.test_action_outcome_report "
            "tests.test_action_outcome_workflow -v",
            self.text,
        )
        self.assertIn('"tests/test_action_outcome_workflow.py"', self.text)
        self.assertIn("zcloud_action_outcome_report.py", self.text)
        self.assertIn("stat -c '%s|%y'", self.text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "REPLACE INTO",
            "DROP TABLE",
            "ALTER TABLE",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
