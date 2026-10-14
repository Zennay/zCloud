"""Trust-boundary contract for portfolio priority normalizer workflow."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "portfolio-priority-normalizer.yml"


class PortfolioPriorityNormalizerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  reconcile:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn(
            "python3 -m unittest -v tests.test_portfolio_priority_normalizer_workflow",
            validate,
        )

    def test_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull_request_target:", self.text)

    def test_live_mutation_is_main_only_and_skips_pull_requests(self):
        reconcile = self.text.split("\n  reconcile:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", reconcile)
        self.assertIn("github.repository == 'Zennay/zCloud'", reconcile)
        self.assertIn("github.ref == 'refs/heads/main'", reconcile)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", reconcile)
        validate = self.text.split("  validate:", 1)[1].split("\n  reconcile:", 1)[0]
        self.assertNotIn("127.0.0.1:8765", validate)
        self.assertNotIn('"/api/portfolio-queue"', validate)
        self.assertNotIn('"/api/dynamic-workers"', validate)

    def test_checkout_is_immutable_exact_and_credential_free(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(
            self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'),
            2,
        )

    def test_runner_guard_precedes_any_queue_mutation(self):
        reconcile = self.text.split("\n  reconcile:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = 'result=post("/api/portfolio-queue",{'
        self.assertIn('test "$(id -un)" = "ubuntu"', reconcile)
        self.assertIn(guard, reconcile)
        self.assertIn(mutation, reconcile)
        self.assertLess(reconcile.index(guard), reconcile.index(mutation))

    def test_live_mutation_is_not_cancelled_mid_run(self):
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.text,
        )

    def test_existing_scope_is_preserved(self):
        for token in (
            '"action":"enqueue"',
            '"action":"drop"',
            'post("/api/dynamic-workers",same,"portfolio-priority-normalizer")',
            "ZCLOUD_PORTFOLIO_PRIORITY_NORMALIZER_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
