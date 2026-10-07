import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-recent-branch-ownership-proof.yml"


class RecentBranchOwnershipWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_owner_same_repo_guard_wraps_self_hosted_execution(self):
        text = self.text
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )

    def test_checkout_is_immutable_exact_head_without_credentials(self):
        text = self.text
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn("ref: ${{ env.EXPECTED_ZCLOUD_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)

    def test_runner_identity_precedes_repository_code(self):
        text = self.text
        identity = text.index("Prove permanent runner and exact checkout")
        regressions = text.index("Run recent-branch ownership regressions")
        full = text.index("Run full zCloud regression parity suite")
        live = text.index("Prove current PR has no recent un-PR branch conflict")
        self.assertLess(identity, regressions)
        self.assertLess(regressions, full)
        self.assertLess(full, live)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_stacked_proof_runs_full_regression_parity(self):
        text = self.text
        required = (
            "python3 -m unittest discover -v",
            "bash tests/test_birds_eye_review.sh",
            "bash tests/test_improvement_stopgate_js.sh",
            "bash tests/test_autonomy_runner_js.sh",
            "node tests/test_reload_extension.mjs",
            "node tests/test_firefox_recovery_contract.js",
            "python3 -m unittest -v tests.test_prechange_guard",
            "python3 -m unittest -v tests.test_config_validation",
            "python3 scripts/zcloud_config_validate.py",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_current_pr_evidence_has_count_and_files(self):
        text = self.text
        self.assertRegex(text, r"--json changedFiles,files")
        self.assertIn("--current-pr-json", text)
        self.assertIn("--require-clear", text)
        self.assertIn("ZCLOUD_RECENT_BRANCH_OWNERSHIP_GREEN=1", text)

    def test_composes_parent_open_pr_overlap_preflight(self):
        text = self.text
        self.assertIn("scripts/zcloud_open_pr_overlap_audit.py", text)
        self.assertIn("--json number,headRefName,isDraft,updatedAt,changedFiles,files", text)
        self.assertIn("--limit 501", text)
        self.assertIn('echo "ZCLOUD_COMPOSITE_OWNERSHIP_PREFLIGHT_GREEN=1"', text)

    def test_branch_snapshot_has_explicit_bounded_recency(self):
        text = self.text
        self.assertIn("--recent-hours 72", text)
        self.assertIn("zcloud_recent_branch_snapshot.py", text)
        self.assertIn("zcloud_recent_branch_ownership_audit.py", text)

    def test_permissions_are_read_only_and_mutation_commands_absent(self):
        text = self.text
        self.assertRegex(
            text,
            r"permissions:\n\s+contents: read\n\s+pull-requests: read",
        )
        forbidden = (
            "contents: write",
            "pull-requests: write",
            "actions: write",
            "git push",
            "git branch -D",
            "git push --delete",
            "gh pr merge",
            "gh pr close",
            "gh api --method DELETE",
            "curl -X POST",
            "curl --request POST",
            "sudo systemctl",
            "systemctl restart",
            "sqlite3 /home/ubuntu/zennay-cloud/history.db",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
