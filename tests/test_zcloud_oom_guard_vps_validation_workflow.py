"""Trust-boundary contract for the zCloud OOM guard VPS validation workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-oom-guard-vps-validation.yml"


class ZcloudOomGuardVpsValidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_on_github_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  probe:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_zcloud_oom_guard_vps_validation_workflow", validate)

    def test_live_probe_is_manual_main_only_on_permanent_runner(self):
        probe = self.text.split("\n  probe:", 1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch'", probe)
        self.assertIn("github.repository == 'Zennay/zCloud'", probe)
        self.assertIn("github.ref == 'refs/heads/main'", probe)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", probe)

    def test_checkout_is_immutable_and_credentials_are_disabled(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)

    def test_runner_guard_precedes_any_candidate_or_live_probe_execution(self):
        probe = self.text.split("\n  probe:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        compile_step = "name: Compile OOM recovery paths"
        regression_step = "name: Run focused worker recovery regression suite"
        live_step = "name: Probe live VPS memory and swap without changing runtime"
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', probe)
        self.assertIn('test "$(id -un)" = "ubuntu"', probe)
        self.assertIn(guard, probe)
        self.assertLess(probe.index(guard), probe.index(compile_step))
        self.assertLess(probe.index(guard), probe.index(regression_step))
        self.assertLess(probe.index(guard), probe.index(live_step))

    def test_pr_validation_has_no_live_vps_reads(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  probe:", 1)[0]
        for token in ("free -h", "swapon --show", "journalctl -k", "WORKER_MEMORY_GUARD="):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_existing_oom_regression_scope_is_preserved(self):
        for token in (
            "tests.test_worker_memory_guard",
            "tests.test_worker_progress_watchdog",
            "tests.test_zcloud_healthcheck",
            "tests.test_self_heal_assets",
            "tests.test_portfolio_queue_backend",
            "tests.test_runner_smoke",
            "server.worker_memory_status()",
            "ZCLOUD_OOM_GUARD_VPS_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
