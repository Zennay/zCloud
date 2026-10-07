"""Trust-boundary contract for the HaxLab multisource queue-close workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "haxlab-multisource-queue-close.yml"


class HaxlabMultisourceQueueCloseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_read_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  close:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        for token in ("history.db", "BEGIN IMMEDIATE", "UPDATE portfolio_queue", "MULTISOURCE_QUEUE_DONE"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_push_scope_does_not_expand_to_test_only_changes(self):
        push = self.text.split("  push:", 1)[1].split("\n  workflow_dispatch:", 1)[0]
        self.assertIn(".github/workflows/haxlab-multisource-queue-close.yml", push)
        self.assertNotIn("tests/test_haxlab_multisource_queue_close_workflow.py", push)

    def test_live_close_requires_canonical_main_and_permanent_runner(self):
        close = self.text.split("\n  close:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", close)
        self.assertIn("github.repository == 'Zennay/zCloud'", close)
        self.assertIn("github.ref == 'refs/heads/main'", close)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", close)

    def test_exact_checkout_and_runner_guard_precede_sqlite_write(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        close = self.text.split("\n  close:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Atomically record completed multisource implementation"
        self.assertIn('test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"', close)
        self.assertIn('test "$(id -un)" = "ubuntu"', close)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', close)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', close)
        self.assertLess(close.index(guard), close.index(mutation))
        self.assertNotIn("ref: main", close)

    def test_live_queue_semantics_are_preserved(self):
        for token in (
            'qid="haxlab-multisource-evaluation-suite-v2"',
            'conn.execute("BEGIN IMMEDIATE")',
            'if row["status"]=="done" and not row["eligible"]',
            'if row["status"]!="queued" or row["worker_slot"] is not None',
            "SET status='done',eligible=0,evidence=?,blocker=''",
            'WHERE queue_id=? AND status=\'queued\' AND worker_slot IS NULL',
            'print("MULTISOURCE_QUEUE_DONE",final)',
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_live_close_is_non_cancelling_and_pr_isolated(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.number || 'live'",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
