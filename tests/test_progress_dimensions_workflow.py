import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-progress-dimensions-proof.yml"


class ProgressDimensionsWorkflowTests(unittest.TestCase):
    def test_proof_uses_exact_head_on_permanent_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            text,
        )

    def test_proof_is_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "systemctl ",
            "sudo ",
            "sqlite3 ",
            "gh api",
            "curl ",
            "/home/ubuntu/zennay-cloud/history.db",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_proof_rejects_title_telemetry_and_checks_all_four_dimensions(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--project ftmo --require-available --json", text)
        self.assertIn('{"research", "build", "validation", "operations"}', text)
        self.assertIn("assert '\"title\"' not in serialized", text)
        self.assertIn("ZCLOUD_PROGRESS_DIMENSIONS_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
