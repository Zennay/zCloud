"""Trust-boundary contract for the HaxLab Arena v2 queue-advance workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "haxlab-arena-v2-queue-advance.yml"


class HaxlabArenaV2QueueAdvanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_cannot_touch_live_control_plane(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  advance:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        for token in (
            "127.0.0.1:8765",
            "/api/portfolio-queue",
            "/api/dynamic-workers",
            "ARENA_QUEUE_DONE",
            "POOL_RECONCILED",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_push_scope_does_not_expand_to_test_only_changes(self):
        push = self.text.split("  push:", 1)[1].split("\n  workflow_dispatch:", 1)[0]
        self.assertIn(".github/workflows/haxlab-arena-v2-queue-advance.yml", push)
        self.assertNotIn("tests/test_haxlab_arena_v2_queue_advance_workflow.py", push)

    def test_live_advance_requires_canonical_main_and_permanent_runner(self):
        advance = self.text.split("\n  advance:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", advance)
        self.assertIn("github.repository == 'Zennay/zCloud'", advance)
        self.assertIn("github.ref == 'refs/heads/main'", advance)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", advance)

    def test_exact_checkout_and_runner_guard_precede_queue_api(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        advance = self.text.split("\n  advance:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Close Arena v2 P1 and claim next HaxLab write lane"
        self.assertIn('test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"', advance)
        self.assertIn('test "$(id -un)" = "ubuntu"', advance)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', advance)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', advance)
        self.assertLess(advance.index(guard), advance.index(mutation))
        self.assertNotIn("ref: main", advance)

    def test_existing_arena_queue_transition_semantics_are_preserved(self):
        for token in (
            'queue_id="haxlab-closed-loop-arena-v2-implementation"',
            '"X-ZCloud-Actor":"haxlab-arena-v2-queue-advance"',
            'get("/api/portfolio-queue?all=1")',
            'not in {"claimed","running","verifying"}',
            '"action":"result"',
            '"result":"DONE"',
            'get("/api/dynamic-workers")',
            'post("/api/dynamic-workers",same)',
            'print("ARENA_QUEUE_DONE"',
            'print("POOL_RECONCILED"',
            'if not active:',
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_live_advance_is_non_cancelling_and_pr_isolated(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.number || 'live'",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
