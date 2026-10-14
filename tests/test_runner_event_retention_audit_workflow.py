import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-runner-event-retention-audit.yml"


class RunnerEventRetentionAuditWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_pr_execution_is_owner_same_repo_and_exact_head_guarded(self):
        text = self.text()
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)

    def test_workflow_only_runs_read_only_retention_inventory(self):
        text = self.text()
        self.assertIn("zcloud_vps_runner_guard.py --json", text)
        self.assertIn(
            "python3 -m unittest -v tests.test_runner_event_retention_audit",
            text,
        )
        self.assertIn(
            "python3 scripts/zcloud_runner_event_retention_audit.py",
            text,
        )
        self.assertIn("--db /home/ubuntu/zennay-cloud/history.db", text)
        for forbidden in (
            "--apply",
            "DELETE FROM",
            "UPDATE ",
            "INSERT ",
            "sudo ",
            "systemctl ",
            "rm -",
            "github.token",
            "secrets.",
        ):
            self.assertNotIn(forbidden, text)

    def test_trigger_is_narrow_and_timeout_bounded(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("timeout-minutes: 4", text)
        self.assertIn("zcloud-runner-event-retention-audit.yml", text)
        self.assertNotIn("push:\n", text)
        self.assertNotIn("schedule:", text)


if __name__ == "__main__":
    unittest.main()
