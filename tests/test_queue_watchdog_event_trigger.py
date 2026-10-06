import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-hosted-queue-watchdog.yml"


class QueueWatchdogEventTriggerTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_watchdog_has_hosted_event_trigger_when_regression_completes(self):
        self.assertIn("workflow_run:", self.text)
        self.assertIn('workflows: ["zCloud regression smoke"]', self.text)
        self.assertIn("types: [completed]", self.text)
        self.assertIn("observe:\n    runs-on: ubuntu-latest", self.text)
        self.assertIn("timeout-minutes: 3", self.text)

    def test_event_trigger_reuses_single_cancel_in_progress_watchdog(self):
        self.assertIn("group: zcloud-self-hosted-queue-watchdog", self.text)
        self.assertIn("cancel-in-progress: true", self.text)
        self.assertIn("cancelled_superseded_runs", self.text)
        self.assertIn("/actions/runs/{item['run_id']}/cancel", self.text)

    def test_recovery_still_only_runs_after_stale_queue_evidence(self):
        self.assertIn("if: needs.observe.outputs.recover == 'true'", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
