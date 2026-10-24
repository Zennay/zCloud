"""Trust-boundary contract for the live worker generation proof workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "prove-workers-generating-now.yml"


class ProveWorkersGeneratingNowWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  prove:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_prove_workers_generating_now_workflow", validate)

    def test_live_proof_skips_prs_and_requires_canonical_main(self):
        prove = self.text.split("\n  prove:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", prove)
        self.assertIn("github.repository == 'Zennay/zCloud'", prove)
        self.assertIn("github.ref == 'refs/heads/main'", prove)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", prove)

    def test_checkout_and_runner_provenance_precede_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        prove = self.text.split("\n  prove:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Force real generation and prove browser consumption"
        self.assertIn('test "$(id -un)" = "ubuntu"', prove)
        self.assertIn(guard, prove)
        self.assertIn(mutation, prove)
        self.assertLess(prove.index(guard), prove.index(mutation))

    def test_pr_validation_cannot_touch_live_runtime(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  prove:", 1)[0]
        for token in ("127.0.0.1:8765", "history.db", "/api/dynamic-workers/force-push", "/api/runner-control"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_proof_cannot_be_cancelled_mid_mutation(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'live'", self.text)

    def test_existing_generation_proof_scope_is_preserved(self):
        for token in (
            "/api/dynamic-workers/force-push",
            "/api/runner-control",
            '"action":"new_chat"',
            "/home/ubuntu/zennay-cloud/history.db",
            "REAL_GENERATION_PROVEN",
            "ZCLOUD_PROVE_WORKERS_GENERATING_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
