"""Trust-boundary contract for the live worker reactivation workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reactivate-workers-now.yml"


class ReactivateWorkersNowWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  reactivate:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_reactivate_workers_now_workflow", validate)

    def test_live_reactivation_skips_prs_and_requires_canonical_main(self):
        reactivate = self.text.split("\n  reactivate:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", reactivate)
        self.assertIn("github.repository == 'Zennay/zCloud'", reactivate)
        self.assertIn("github.ref == 'refs/heads/main'", reactivate)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", reactivate)

    def test_checkout_and_runner_provenance_precede_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        reactivate = self.text.split("\n  reactivate:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Recover browser runtime and start allocated workers"
        self.assertIn('test "$(id -un)" = "ubuntu"', reactivate)
        self.assertIn(guard, reactivate)
        self.assertIn(mutation, reactivate)
        self.assertLess(reactivate.index(guard), reactivate.index(mutation))

    def test_pr_validation_cannot_touch_live_runtime(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  reactivate:", 1)[0]
        for token in ("127.0.0.1:8765", "systemctl", "/api/runner-control", "new_chat", "kill -TERM"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_reactivation_is_non_cancelling(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'live'", self.text)

    def test_existing_reactivation_scope_is_preserved(self):
        for token in (
            "chatgpt-display.service",
            "chatgpt-openbox.service",
            "chatgpt-firefox.service",
            "/api/dynamic-workers",
            '/api/runner-control',
            '"action":"start"',
            "ZCLOUD_REACTIVATE_WORKERS_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
