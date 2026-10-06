import unittest

from scripts.zcloud_hosted_queue_sweep_policy import classify_run


class HostedQueueSweepPolicyTests(unittest.TestCase):
    def test_unknown_workflow_fails_closed(self):
        decision = classify_run(
            path=".github/workflows/future-self-hosted-job.yml",
            name="Future self-hosted job",
            status="queued",
            age_seconds=99999,
        )
        self.assertFalse(decision.cancel)
        self.assertEqual(decision.reason, "unclassified_fail_closed")

    def test_mutating_deploy_is_never_auto_cancelled(self):
        decision = classify_run(
            path=".github/workflows/zcloud-vps-deploy.yml",
            name="zCloud VPS deploy",
            status="in_progress",
            age_seconds=99999,
        )
        self.assertFalse(decision.cancel)
        self.assertEqual(decision.reason, "protected_mutation")

    def test_known_stale_queued_probe_can_be_cancelled(self):
        decision = classify_run(
            path=".github/workflows/zcloud-vps-execution-probe.yml",
            name="zCloud VPS execution lane probe",
            status="queued",
            age_seconds=481,
        )
        self.assertTrue(decision.cancel)
        self.assertEqual(decision.reason, "stale_queued_safe_lane")

    def test_known_running_probe_gets_longer_budget(self):
        recent = classify_run(
            path=".github/workflows/vps-cpu-diagnostic.yml",
            name="VPS CPU diagnostic snapshot",
            status="in_progress",
            age_seconds=1200,
        )
        stale = classify_run(
            path=".github/workflows/vps-cpu-diagnostic.yml",
            name="VPS CPU diagnostic snapshot",
            status="in_progress",
            age_seconds=1801,
        )
        self.assertFalse(recent.cancel)
        self.assertEqual(recent.reason, "active_safe_lane_within_budget")
        self.assertTrue(stale.cancel)
        self.assertEqual(stale.reason, "stale_in_progress_safe_lane")

    def test_recent_queued_probe_is_retained(self):
        decision = classify_run(
            path=".github/workflows/zguard-vps-verification.yml",
            name="zGuard VPS verification",
            status="queued",
            age_seconds=100,
        )
        self.assertFalse(decision.cancel)
        self.assertEqual(decision.reason, "recent_queued")


if __name__ == "__main__":
    unittest.main()
