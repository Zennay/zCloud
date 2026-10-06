import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-iteration-hypothesis-audit.yml"


class IterationHypothesisAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_proof_is_same_repo_owner_and_permanent_vps_only(self):
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_checkout_is_immutable_exact_head_without_credentials(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_live_audit_is_read_only_and_report_only(self):
        live = self.text[
            self.text.index("Audit live zCloud hypothesis coverage read-only") :
        ]
        self.assertIn("zcloud_iteration_hypothesis_audit.py", live)
        self.assertIn("--db /home/ubuntu/zennay-cloud/history.db", live)
        self.assertNotIn("--require-complete", live)
        self.assertIn('test "$before" = "$after"', live)
        for forbidden in (
            "sudo ",
            "sqlite3 ",
            "UPDATE ",
            "INSERT ",
            "DELETE ",
            "systemctl ",
            "rm -",
            "curl -X",
        ):
            self.assertNotIn(forbidden, live)

    def test_repository_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)


if __name__ == "__main__":
    unittest.main()
