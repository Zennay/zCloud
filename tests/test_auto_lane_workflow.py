import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-auto-lane-generator.yml")


class AutoLaneWorkflowTests(unittest.TestCase):
    def test_self_hosted_execution_is_trusted_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "if: github.event_name != 'pull_request' || (github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository)",
            text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_workflow_has_read_only_permissions_and_bounded_concurrency(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("timeout-minutes: 8", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl ", text)

    def test_existing_lane_regression_surface_is_preserved(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python3 -m py_compile lane_generator.py server.py", text)
        self.assertIn("python3 -m unittest -v tests.test_lane_generator", text)
        self.assertIn(
            "python3 -m unittest -v tests.test_portfolio_queue_backend", text
        )
        self.assertIn("python3 -m unittest -v tests.test_task_claims", text)
        self.assertIn("python3 -m unittest -v tests.test_auto_lane_workflow", text)


if __name__ == "__main__":
    unittest.main()
