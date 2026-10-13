"""Trust-boundary contract for automatic lane regression CI."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-auto-lane-generator.yml"


class ZcloudAutoLaneGeneratorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  lane-regression:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_lane_generator", validate)
        self.assertIn("tests.test_zcloud_auto_lane_generator_workflow", validate)

    def test_self_hosted_lane_runs_only_outside_pull_requests(self):
        lane = self.text.split("\n  lane-regression:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", lane)
        self.assertIn("github.repository == 'Zennay/zCloud'", lane)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", lane)

    def test_exact_checkout_and_runner_guard_precede_tests(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        lane = self.text.split("\n  lane-regression:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        tests = "Prove deterministic non-overlapping lane allocation"
        self.assertIn('test "$(id -un)" = "ubuntu"', lane)
        self.assertLess(lane.index(guard), lane.index(tests))

    def test_existing_trigger_and_regression_scope_is_preserved(self):
        for token in (
            "branches: [main, 'worker/**']",
            "lane_generator.py",
            "tests/test_lane_generator.py",
            "tests/test_portfolio_queue_backend.py",
            "tests/test_task_claims.py",
            "python3 -m py_compile lane_generator.py server.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_pr_job_has_no_live_control_plane_access(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  lane-regression:", 1)[0]
        for token in ("127.0.0.1:8765", "history.db", "systemctl", "/api/", "sudo -n"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_self_hosted_success_marker_is_explicit(self):
        lane = self.text.split("\n  lane-regression:", 1)[1]
        self.assertIn("ZCLOUD_AUTO_LANE_REGRESSION_GREEN=1", lane)


if __name__ == "__main__":
    unittest.main()
