"""Trust-boundary contract for the worker watchdog deploy workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-watchdog-deploy.yml"


class ZcloudWorkerWatchdogDeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  deploy:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_worker_progress_watchdog", validate)
        self.assertIn("tests.test_zcloud_worker_watchdog_deploy_workflow", validate)
        for token in ("sudo -n", "systemctl", "/usr/local/sbin", "/etc/systemd/system"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_push_scope_does_not_expand_to_trust_test_only_changes(self):
        push = self.text.split("  push:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("tests/test_worker_progress_watchdog.py", push)
        self.assertIn(".github/workflows/zcloud-worker-watchdog-deploy.yml", push)
        self.assertNotIn("tests/test_zcloud_worker_watchdog_deploy_workflow.py", push)

    def test_live_deploy_requires_canonical_main_and_permanent_runner(self):
        deploy = self.text.split("\n  deploy:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", deploy)
        self.assertIn("github.repository == 'Zennay/zCloud'", deploy)
        self.assertIn("github.ref == 'refs/heads/main'", deploy)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", deploy)

    def test_exact_checkout_and_runner_guard_precede_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        deploy = self.text.split("\n  deploy:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Install watchdog into the existing one-minute self-heal loop"
        self.assertIn('test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"', deploy)
        self.assertIn('test "$(id -un)" = "ubuntu"', deploy)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', deploy)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', deploy)
        self.assertIn(guard, deploy)
        self.assertIn(mutation, deploy)
        self.assertLess(deploy.index(guard), deploy.index(mutation))
        self.assertNotIn("ref: main", deploy)

    def test_live_deploy_cannot_be_cancelled_mid_mutation(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.number || 'live'",
            self.text,
        )

    def test_remote_actions_are_immutable(self):
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            self.text,
        )
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertNotIn("actions/upload-artifact@v4", self.text)

    def test_existing_watchdog_deploy_semantics_are_preserved(self):
        for token in (
            "python3 -m unittest tests.test_worker_progress_watchdog",
            "sudo -n install -m 0755 scripts/zcloud_worker_watchdog.py /usr/local/sbin/zcloud-worker-watchdog",
            "sudo -n install -m 0755 scripts/zcloud-self-heal.sh /usr/local/sbin/zcloud-self-heal",
            "sudo -n install -m 0644 deploy/zcloud-self-heal.service /etc/systemd/system/zcloud-self-heal.service",
            "sudo -n install -m 0644 deploy/zcloud-self-heal.timer /etc/systemd/system/zcloud-self-heal.timer",
            "sudo -n systemctl enable --now zcloud-self-heal.timer",
            "sudo -n /usr/local/sbin/zcloud-worker-watchdog --dry-run --json",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
