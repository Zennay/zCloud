import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-watchdog-deploy.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"
UPLOAD_V7 = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1"


class WorkerWatchdogDeployWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only_and_non_mutating(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, synchronize, reopened]", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn(CHECKOUT_V6, self.text)
        validate = self.text[
            self.text.index("  validate:") : self.text.index("\n  deploy:")
        ]
        for forbidden in (
            "sudo -n install",
            "systemctl ",
            "/usr/local/sbin",
            "/etc/systemd/system",
        ):
            self.assertNotIn(forbidden, validate)

    def test_live_deploy_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("deploy:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: self-hosted\n", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(self.text.count(CHECKOUT_V6), 2)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        first_install = "sudo -n install -m 0755 scripts/zcloud_worker_watchdog.py"
        deploy_start = self.text.index("  deploy:")
        self.assertLess(
            self.text.index(exact, deploy_start),
            self.text.index(guard, deploy_start),
        )
        self.assertLess(
            self.text.index(guard, deploy_start),
            self.text.index(first_install, deploy_start),
        )

    def test_snapshot_precedes_writes_and_covers_all_installed_files(self):
        snapshot = self.text.index("Snapshot current watchdog runtime")
        install = self.text.index("Install watchdog into the existing one-minute self-heal loop")
        self.assertLess(snapshot, install)
        for token in (
            "watchdog:/usr/local/sbin/zcloud-worker-watchdog",
            "self_heal:/usr/local/sbin/zcloud-self-heal",
            "service:/etc/systemd/system/zcloud-self-heal.service",
            "timer:/etc/systemd/system/zcloud-self-heal.timer",
            'echo "was_enabled=$enabled" >> "$GITHUB_OUTPUT"',
            'echo "was_active=$active" >> "$GITHUB_OUTPUT"',
        ):
            self.assertIn(token, self.text)

    def test_failure_rollback_restores_files_and_timer_state(self):
        self.assertIn("if: failure() && steps.snapshot.outcome == 'success'", self.text)
        self.assertIn("HAD_WATCHDOG: ${{ steps.snapshot.outputs.had_watchdog }}", self.text)
        self.assertIn("HAD_SELF_HEAL: ${{ steps.snapshot.outputs.had_self_heal }}", self.text)
        self.assertIn("HAD_SERVICE: ${{ steps.snapshot.outputs.had_service }}", self.text)
        self.assertIn("HAD_TIMER: ${{ steps.snapshot.outputs.had_timer }}", self.text)
        self.assertIn("WAS_ENABLED: ${{ steps.snapshot.outputs.was_enabled }}", self.text)
        self.assertIn("WAS_ACTIVE: ${{ steps.snapshot.outputs.was_active }}", self.text)
        self.assertIn("restore_file \"$HAD_WATCHDOG\"", self.text)
        self.assertIn("restore_file \"$HAD_SELF_HEAL\"", self.text)
        self.assertIn("restore_file \"$HAD_SERVICE\"", self.text)
        self.assertIn("restore_file \"$HAD_TIMER\"", self.text)
        self.assertIn("ZCLOUD_WORKER_WATCHDOG_DEPLOY_ROLLBACK_ATTEMPTED=1", self.text)

    def test_live_deploys_serialize_but_pr_validation_has_separate_group(self):
        self.assertIn(
            "group: zcloud-worker-progress-watchdog-deploy-${{ github.event_name == 'pull_request' && github.ref || 'live' }}",
            self.text,
        )
        self.assertIn("cancel-in-progress: false", self.text)

    def test_remote_actions_are_immutable_and_logs_are_bounded(self):
        self.assertIn(UPLOAD_V7, self.text)
        self.assertIn("path: ${{ runner.temp }}/zcloud-worker-watchdog-dry-run.json", self.text)
        self.assertNotIn("actions/upload-artifact@v4", self.text)
        self.assertNotIn("journalctl -u zcloud-self-heal.service", self.text)
        self.assertIn(
            "systemctl show zcloud-self-heal.timer",
            self.text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
