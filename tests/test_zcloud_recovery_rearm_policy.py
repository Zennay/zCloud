"""Offline tests only; no production token, database, or workflow mutation."""
import unittest

from scripts.zcloud_recovery_rearm_policy import Run, decide_rearm

S = "a" * 40
T = "b" * 40
def run(sha=S, event="push", status="completed", conclusion="success", branch="main"):
    return Run(sha, branch, event, status, conclusion)
def decide(**overrides):
    inputs = dict(sha=S, production_status_present=False, deploy_runs=(), regression_runs=(run(),))
    inputs.update(overrides)
    return decide_rearm(**inputs)


class RecoveryRearmPolicyTests(unittest.TestCase):
    def test_initial_exact_green_candidate(self):
        self.assertEqual(decide(), "eligible_for_owner_review")

    def test_repeat_ticks_dedupe_completed_dispatch(self):
        for _ in range(3):
            self.assertEqual(decide(regression_runs=(run(), run(event="workflow_dispatch"))), "noop_already_rearmed")

    def test_failed_deploy_does_not_restart_forever(self):
        self.assertEqual(decide(deploy_runs=(run(event="workflow_dispatch", conclusion="failure"),)), "deny_failed_deploy")

    def test_active_deploy_and_regression(self):
        self.assertEqual(decide(deploy_runs=(run(status="in_progress", conclusion=""),)), "wait_active_deploy")
        self.assertEqual(decide(regression_runs=(run(), run(status="queued", conclusion="")),), "wait_active_regression")

    def test_new_main_sha_ignores_old_markers(self):
        self.assertEqual(decide(sha=T, regression_runs=(run(sha=T), run(event="workflow_dispatch"))), "eligible_for_owner_review")

    def test_wrong_branch_and_wrong_sha_never_count(self):
        self.assertEqual(decide(regression_runs=(run(branch="other"),)), "wait_exact_green")
        self.assertEqual(decide(regression_runs=(run(sha=T),)), "wait_exact_green")

    def test_explicit_retry_not_implicit(self):
        self.assertEqual(decide(regression_runs=(run(), run(event="workflow_dispatch")), explicit_retry_approved=True), "eligible_for_owner_review")

    def test_absent_status_and_invalid_sha_are_fail_closed(self):
        self.assertEqual(decide(production_status_present=True), "noop_production_status")
        self.assertEqual(decide(sha="main"), "deny_invalid_sha")


if __name__ == "__main__":
    unittest.main()
