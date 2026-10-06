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

    def test_hosted_observe_and_vps_recover_have_separate_concurrency(self):
        self.assertNotIn("\nconcurrency:\n  group: zcloud-self-hosted-queue-watchdog\n", self.text)
        self.assertIn("group: zcloud-self-hosted-queue-watchdog-observe", self.text)
        self.assertIn("group: zcloud-self-hosted-queue-watchdog-recover", self.text)
        self.assertEqual(2, self.text.count("cancel-in-progress: false"))
        self.assertIn("cancelled_superseded_runs", self.text)
        self.assertIn("/actions/runs/{item['run_id']}/cancel", self.text)

    def test_recovery_still_only_runs_after_stale_queue_evidence(self):
        self.assertIn("if: needs.observe.outputs.recover == 'true'", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
