import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-hosted-queue-watchdog.yml"


class QueueWatchdogRecoveryTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_live_recovery_is_bound_to_permanent_vps(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("recover:\n    needs: observe\n    if: needs.observe.outputs.recover == 'true'\n    runs-on: self-hosted", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_exact_revision_is_proved_before_root_mutation(self):
        checkout = self.text.index("Checkout exact watchdog revision")
        guard = self.text.index("Verify permanent VPS runner before recovery")
        recover = self.text.index("Recover idle zCloud listener and push work")
        restart = self.text.index('sudo -n systemctl restart "$service"')
        self.assertLess(checkout, guard)
        self.assertLess(guard, recover)
        self.assertLess(recover, restart)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)

    def test_workflow_run_live_path_accepts_only_trusted_main_runs(self):
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", self.text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.text)
        self.assertIn("github.event.workflow_run.event == 'push'", self.text)
        self.assertIn("github.event.workflow_run.event == 'workflow_dispatch'", self.text)

    def test_permissions_are_scoped_to_hosted_observer(self):
        top = self.text.split("jobs:", 1)[0]
        self.assertNotIn("actions: write", top)
        observe = self.text.split("  observe:", 1)[1].split("  recover:", 1)[0]
        recover = self.text.split("  recover:", 1)[1]
        self.assertIn("actions: write", observe)
        self.assertNotIn("actions: write", recover)
        self.assertIn("contents: read", recover)

    def test_recovery_logs_only_bounded_evidence(self):
        self.assertNotIn('echo "QUEUE_EVIDENCE=$QUEUE_EVIDENCE"', self.text)
        self.assertNotIn('json.dumps(reconciled,sort_keys=True)', self.text)
        self.assertNotIn('json.dumps(pushed,sort_keys=True)', self.text)
        self.assertIn("ZCLOUD_QUEUE_EVIDENCE", self.text)
        self.assertIn("cancelled_superseded_count=", self.text)
        self.assertIn("WORKER_CAPACITY_RECONCILED ", self.text)
        self.assertIn("WORKERS_FORCE_PUSHED ok=", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
