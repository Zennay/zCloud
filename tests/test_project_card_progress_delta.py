import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENHANCEMENTS = ROOT / "public" / "enhancements.js"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-card-progress-delta.yml"


class ProjectCardProgressDeltaTests(unittest.TestCase):
    def test_behavioral_node_contract(self):
        result = subprocess.run(
            ["node", "tests/test_project_card_progress_delta.js"],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Project card progress session delta contract passed.", result.stdout)

    def test_previous_session_cache_is_bounded_and_read_only(self):
        text = ENHANCEMENTS.read_text(encoding="utf-8")
        self.assertIn("PREVIOUS_PROGRESS_CACHE_KEY='zcloud:last-status:v1'", text)
        self.assertIn("PREVIOUS_PROGRESS_MAX_AGE_MS=24*60*60*1000", text)
        self.assertIn("projects.length>100", text)
        self.assertIn("/^[a-z0-9][a-z0-9_-]{0,79}$/", text)
        self.assertIn("value<0||value>100", text)
        self.assertNotIn("localStorage.setItem(PREVIOUS_PROGRESS_CACHE_KEY", text)

    def test_session_delta_precedes_range_fallback_and_labels_both(self):
        text = ENHANCEMENTS.read_text(encoding="utf-8")
        session = text.index("var delta=previousSessionProgressDelta(p)")
        history = text.index("delta=progressDelta(p)", session)
        self.assertLess(session, history)
        self.assertIn("context:'last session'", text)
        self.assertIn("'24h':'24h','7d':'7d','30d':'30d','all':'all history'", text)
        self.assertIn("+' pp · '+esc(deltaModel.context)", text)

    def test_workflow_is_exact_head_and_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        for forbidden in ("sudo ", "systemctl ", "sqlite3 ", "curl -X", "gh api --method"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
