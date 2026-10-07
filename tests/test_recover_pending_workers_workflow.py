"""Trust-boundary contract for the pending-worker recovery workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "recover-pending-workers.yml"


class RecoverPendingWorkersWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  recover:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_recover_pending_workers_workflow", validate)

    def test_live_recovery_skips_prs_and_requires_canonical_main(self):
        recover = self.text.split("\n  recover:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", recover)
        self.assertIn("github.repository == 'Zennay/zCloud'", recover)
        self.assertIn("github.ref == 'refs/heads/main'", recover)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", recover)

    def test_checkout_and_runner_provenance_precede_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        recover = self.text.split("\n  recover:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Inspect allocation and recover inactive workers"
        self.assertIn('test "$(id -un)" = "ubuntu"', recover)
        self.assertIn(guard, recover)
        self.assertIn(mutation, recover)
        self.assertLess(recover.index(guard), recover.index(mutation))

    def test_pr_validation_cannot_touch_live_runtime(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  recover:", 1)[0]
        for token in ("127.0.0.1:8765", "/api/runner-control", "history.db", "systemctl", "new_chat"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_recovery_is_non_cancelling(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'live'", self.text)

    def test_existing_recovery_scope_is_preserved(self):
        for token in (
            '/api/runner-targets',
            '/api/runner-control',
            '"action":"start"',
            '"action":"new_chat"',
            "not all allocated workers became active",
            "ZCLOUD_RECOVER_PENDING_WORKERS_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
