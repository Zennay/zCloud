import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-one-worker-testing-activation.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class DynamicWorkerActivationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_live_activation_is_skipped(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn(CHECKOUT_V6, self.text)
        validate = self.text[
            self.text.index("  validate:") : self.text.index("\n  activate:")
        ]
        for forbidden in (
            "/api/runner-control",
            "systemctl ",
            "pgrep ",
            "chatgpt-firefox.service",
        ):
            self.assertNotIn(forbidden, validate)

    def test_live_activation_is_exact_deploy_sha_and_permanent_runner_guarded(self):
        self.assertIn("github.event_name == 'workflow_run'", self.text)
        self.assertIn("github.event.workflow_run.head_sha", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: self-hosted\n", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(self.text.count(CHECKOUT_V6), 2)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        first_mutation = "systemctl --user restart chatgpt-firefox.service"
        activate_start = self.text.index("  activate:")
        self.assertLess(self.text.index(exact, activate_start), self.text.index(guard, activate_start))
        self.assertLess(self.text.index(guard, activate_start), self.text.index(first_mutation, activate_start))

    def test_logs_do_not_dump_process_cmdlines_or_raw_runtime_payloads(self):
        self.assertNotIn("pgrep -af", self.text)
        self.assertIn("FIREFOX_PROCESS_COUNT=", self.text)
        self.assertNotIn("FINAL_RUNNER_TARGETS=", self.text)
        self.assertNotIn("FINAL_STATUS=", self.text)
        self.assertIn("FINAL_LIVE_SUMMARY=", self.text)
        self.assertNotIn("start_commands", self.text)
        self.assertIn("start_command_ids", self.text)
        self.assertNotIn("detail[:1000]", self.text)
        self.assertNotIn("command failed for {key}: {command}", self.text)
        self.assertNotIn("dispatch blocked for {key}:", self.text)

    def test_live_summary_is_bounded_to_control_metadata(self):
        self.assertIn("def worker_summary(status, key):", self.text)
        for field in (
            '"state"',
            '"age_seconds"',
            '"generating"',
            '"last_event"',
            '"command_id"',
            '"command_status"',
        ):
            self.assertIn(field, self.text)
        for forbidden in (
            '"reason":',
            '"error":',
            '"result":',
            '"target":',
            '"prompt":',
        ):
            self.assertNotIn(forbidden, self.text)

    def test_pr_checks_cannot_cancel_live_activation(self):
        self.assertIn(
            "group: zcloud-dynamic-worker-pool-activation-${{ github.event_name }}-${{ github.ref }}",
            self.text,
        )
        self.assertIn("ZCLOUD_DYNAMIC_WORKER_ACTIVATION_GREEN=disabled", self.text)
        self.assertIn("ZCLOUD_DYNAMIC_WORKER_ACTIVATION_GREEN=idle", self.text)
        self.assertIn("ZCLOUD_DYNAMIC_WORKER_ACTIVATION_GREEN=active", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
