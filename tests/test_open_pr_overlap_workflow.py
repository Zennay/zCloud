import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-open-pr-overlap-proof.yml"


class OpenPrOverlapWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_execution_is_owner_same_repo_guarded(self):
        text = self.text
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )

    def test_checkout_is_immutable_exact_head_without_persisted_credentials(self):
        text = self.text
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("EXPECTED_ZCLOUD_SHA:", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn("ref: ${{ env.EXPECTED_ZCLOUD_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)

    def test_permanent_runner_identity_is_proved_before_tests_and_snapshot(self):
        text = self.text
        identity = text.index("Prove permanent runner and exact checkout")
        tests = text.index("Run focused ownership-audit regressions")
        snapshot = text.index("Audit current open PR file ownership")
        self.assertLess(identity, tests)
        self.assertLess(identity, snapshot)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_open_pr_snapshot_excludes_free_text_fields(self):
        text = self.text
        match = re.search(r"--json ([^\n]+)", text)
        self.assertIsNotNone(match)
        fields = match.group(1)
        self.assertIn("number", fields)
        self.assertIn("headRefName", fields)
        self.assertIn("files", fields)
        for forbidden in ("title", "body", "comments", "author", "reviews"):
            self.assertNotIn(forbidden, fields)

    def test_pr_proof_requires_collision_free_current_slice(self):
        text = self.text
        self.assertIn("--current-pr", text)
        self.assertIn('"$CURRENT_PR"', text)
        self.assertIn("--require-clear", text)
        self.assertIn("ZCLOUD_OPEN_PR_OVERLAP_AUDIT_GREEN=1", text)

    def test_workflow_has_read_only_repository_permissions_and_no_mutation_commands(self):
        text = self.text
        self.assertRegex(text, r"permissions:\n\s+contents: read\n\s+pull-requests: read")
        forbidden = (
            "contents: write",
            "pull-requests: write",
            "sudo systemctl",
            "systemctl restart",
            "curl -X POST",
            "curl --request POST",
            "sqlite3 /home/ubuntu/zennay-cloud/history.db",
            "git push",
            "gh pr merge",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
