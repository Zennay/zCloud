import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-backlog-selection-proof.yml"


class BacklogSelectionWorkflowTests(unittest.TestCase):
    def test_hosted_then_permanent_vps_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permanent-vps-proof:\n    needs: validate", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertGreaterEqual(text.count("persist-credentials: false"), 2)

    def test_workflow_is_policy_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "gh api",
            "curl ",
            "sqlite3 ",
            "INSERT ",
            "UPDATE ",
            "DELETE ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_vps_fixture_requires_safe_candidate(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"selected_id"] == "high-safe"', text)
        self.assertIn('{"already_claimed": 1}', text)
        self.assertIn("ZCLOUD_BACKLOG_SELECTION_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
