import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-runner-event-retention-proof.yml"


class RunnerEventRetentionWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_live_proof_is_same_repo_owner_guarded_and_exact_head(self):
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
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)

    def test_live_proof_can_only_dry_run_retention(self):
        text = self.text()
        self.assertIn(
            "python3 scripts/zcloud_runner_event_retention.py",
            text,
        )
        self.assertIn("--retention-hours 192", text)
        self.assertIn("--batch-limit 5000", text)
        self.assertNotIn("--apply", text)
        self.assertNotIn("PRUNE_BOUNDED_RUNNER_TELEMETRY", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "DELETE FROM",
            "VACUUM",
            "rm -",
            "contents: write",
            "github.token",
            "secrets.",
        ):
            self.assertNotIn(forbidden, text)

    def test_trigger_is_bounded_and_not_scheduled(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("timeout-minutes: 4", text)
        self.assertNotIn("push:\n", text)
        self.assertNotIn("schedule:", text)


if __name__ == "__main__":
    unittest.main()
