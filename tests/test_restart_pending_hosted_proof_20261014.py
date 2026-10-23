"""Hosted trust tripwire for restart-reconciliation proof (deploy-ops #633).

The workflow runs offline checks against source; no live restart or VPS
evidence is required. These tests must stay independent of secrets and services.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-restart-workers-pending-reconcile-proof.yml"


class RestartPendingHostedProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.job = cls.source.split("  prove:\n", 1)[1]

    def test_pull_request_and_main_push_remain_tested(self):
        self.assertIn("  pull_request:\n", self.source)
        self.assertIn("  push:\n", self.source)
        self.assertIn("branches: [main]", self.source)
        self.assertIn("  workflow_dispatch:", self.source)

    def test_offline_proof_never_reserves_a_permanent_runner(self):
        self.assertIn("runs-on: ubuntu-latest", self.job)
        self.assertNotIn("runs-on: self-hosted", self.source)
        self.assertNotIn("runs-on: [self-hosted", self.source)
        self.assertNotIn("zcloud_vps_runner_guard.py", self.source)

    def test_source_proof_is_read_only_without_runtime_calls(self):
        self.assertIn("contents: read", self.source)
        for unsafe in ("actions: write", "statuses: write", "sudo -n",
                       "systemctl ", "gh run cancel", "/api/dynamic-workers",
                       "gh workflow run", "curl -X POST"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, self.job)

    def test_denies_untrusted_repository_and_forked_pr(self):
        self.assertIn("github.repository == 'Zennay/zCloud'", self.job)
        self.assertIn("github.actor == 'Zennay'", self.job)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.job)

    def test_checkout_pins_revision_without_credentials(self):
        self.assertRegex(self.job, r"uses: actions/checkout@[0-9a-f]{40}")
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.job)
        self.assertIn("persist-credentials: false", self.job)
        self.assertIn("clean: true", self.job)
        self.assertIn('test "$actual" = "$EXPECTED_ZCLOUD_SHA"', self.job)
        self.assertIn("EXPECTED_ZCLOUD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", self.job)

    def test_three_original_reconciliation_suites_are_preserved(self):
        for test in ("tests.test_restart_workers_firefox_timeout",
                     "tests.test_restart_workers_allocation_churn",
                     "tests.test_restart_workers_pending_reconcile"):
            with self.subTest(test=test):
                self.assertIn(test, self.job)
        self.assertIn("tests.test_restart_pending_hosted_proof_20261014", self.job)

    def test_workflow_and_test_edits_both_trigger_checks(self):
        for path in (
            "zcloud-restart-workers.yml",
            "zcloud-restart-workers-pending-reconcile-proof.yml",
            "test_restart_workers_pending_reconcile.py",
            "test_restart_workers_firefox_timeout.py",
            "test_restart_workers_allocation_churn.py",
            "test_restart_pending_hosted_proof_20261014.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(2, self.source.count(path), path)

    def test_source_success_does_not_claim_live_recovery(self):
        self.assertIn("ZCLOUD_RESTART_PENDING_RECONCILE_PROOF_GREEN", self.source)
        self.assertNotIn("ZCLOUD_VPS_DEPLOYED", self.source)
        self.assertNotIn("PRODUCTION_RECOVERY_GREEN", self.source)
        self.assertNotIn("systemctl restart", self.source)


if __name__ == "__main__":
    unittest.main()
