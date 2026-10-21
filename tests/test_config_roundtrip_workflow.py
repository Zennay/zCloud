import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-config-roundtrip-regression.yml"


class ConfigRoundTripWorkflowTests(unittest.TestCase):
    def test_regression_is_exact_head_permanent_vps_and_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            text,
        )
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in ("sudo ", "systemctl ", "sqlite3 ", "curl ", "gh api"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
