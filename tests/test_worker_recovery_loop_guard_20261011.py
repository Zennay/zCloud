"""Prevent timed global worker resets and untrusted queue-recovery events."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
RESTART = ROOT / ".github/workflows/zcloud-restart-workers.yml"
QUEUE = ROOT / ".github/workflows/zcloud-self-hosted-queue-watchdog.yml"


class WorkerRecoveryLoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.restart = RESTART.read_text(encoding="utf-8")
        self.queue = QUEUE.read_text(encoding="utf-8")

    def test_browser_recycling_is_explicit_owner_manual_only(self):
        header = self.restart.split("\npermissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", header)
        for trigger in ("  schedule:", "  push:", "  workflow_run:"):
            self.assertNotIn(trigger, header)

    def test_queue_recovery_does_not_change_config_or_force_browser_push(self):
        self.assertIn("WORKER_POOL_PRESERVED", self.queue)
        self.assertIn('call("GET","/api/dynamic-workers")', self.queue)
        self.assertNotIn('call("POST","/api/dynamic-workers"', self.queue)
        self.assertNotIn('call("POST","/api/dynamic-workers/force-push"', self.queue)
        self.assertNotIn('desired["chatgpt_count"]=7', self.queue)
        self.assertNotIn('sudo -n systemctl restart "firefox', self.queue)

    def test_untrusted_regression_cannot_trigger_privileged_watch(self):
        observe = self.queue.split("  observe:\n", 1)[1].split("\n  recover:", 1)[0]
        head = observe.split("    steps:", 1)[0]
        self.assertIn("github.event.workflow_run.event == 'push'", head)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", head)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", head)
        self.assertIn("github.event.workflow_run.head_sha == github.sha", head)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.actor == 'Zennay'", head)
        self.assertIn("github.repository == 'Zennay/zCloud'", head)
        self.assertIn("github.ref == 'refs/heads/main'", head)

    def test_listener_only_recovers_when_not_processing_jobs(self):
        self.assertIn('if [[ "${#workers[@]}" -eq 0 ]]; then', self.queue)
        self.assertIn("ZCLOUD_QUEUE_RECOVERY=preserve_active_worker", self.queue)
        self.assertIn('test "${#listeners[@]}" -eq 1', self.queue)


if __name__ == "__main__":
    unittest.main()
