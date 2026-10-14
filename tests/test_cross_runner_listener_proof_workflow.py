import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-runner-cross-lane-proof.yml")


class CrossRunnerListenerProofWorkflowTests(unittest.TestCase):
    def test_pr_self_hosted_execution_is_owner_and_same_repo_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "if: github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("ZCLOUD_CROSS_RUNNER_IDENTITY", text)

    def test_proof_does_not_checkout_or_execute_pr_repository_code(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("actions/checkout@", text)
        self.assertNotIn("git checkout", text)
        self.assertNotIn("github.event.pull_request.head.ref", text)
        self.assertNotIn("github.event.pull_request.head.sha", text)

    def test_existing_listener_isolation_proof_is_preserved(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("actions.runner.Zennay-zCloud.zcloud-vps-1.service", text)
        self.assertIn("test \"${#listeners[@]}\" -eq 1", text)
        self.assertIn("ZCLOUD_CROSS_RUNNER_LISTENER_GREEN", text)
        self.assertIn(".zcloud-cross-runner-proof-$GITHUB_RUN_ID", text)
        self.assertIn('test "$result" = "success"', text)
        self.assertIn('test "$exit_status" = "0"', text)


if __name__ == "__main__":
    unittest.main()
