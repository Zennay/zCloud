"""Trust-boundary contract for the scheduled/manual restart-workers workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"

class ZcloudRestartWorkersWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  restart:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_zcloud_restart_workers_workflow", validate)

    def test_live_restart_skips_prs_and_requires_main(self):
        restart = self.text.split("\n  restart:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", restart)
        self.assertIn("github.repository == 'Zennay/zCloud'", restart)
        self.assertIn("github.ref == 'refs/heads/main'", restart)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", restart)

    def test_checkout_and_runner_provenance_precede_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        restart = self.text.split("\n  restart:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Queue fresh-chat restart for allocated workers"
        self.assertIn('test "$(id -un)" = "ubuntu"', restart)
        self.assertIn(guard, restart)
        self.assertIn(mutation, restart)
        self.assertLess(restart.index(guard), restart.index(mutation))

    def test_live_restart_is_non_cancelling_and_pr_has_separate_group(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'live'", self.text)

    def test_pr_validation_cannot_touch_live_runtime(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  restart:", 1)[0]
        for token in ("127.0.0.1:8765", "history.db", "systemctl --user", "SIGTERM", "new_chat"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_existing_restart_scope_is_preserved(self):
        for token in (
            '"action": "restart_firefox"',
            '/api/dynamic-workers',
            '"new_chat", "pending"',
            "ZCLOUD_RESTART_WORKERS_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

if __name__ == "__main__":
    unittest.main()
