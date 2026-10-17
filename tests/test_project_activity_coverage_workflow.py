from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-activity-coverage-proof.yml"


class ProjectActivityCoverageWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_read_only_permanent_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn("ZCLOUD_PROJECT_ACTIVITY_COVERAGE_READONLY_GREEN", text)
        self.assertIn('before_size="$(stat -c %s "$LIVE_DB")"', text)
        self.assertIn('before_mtime="$(stat -c %Y "$LIVE_DB")"', text)
        for forbidden in ("--apply", "sudo ", "systemctl ", "rm -", "DELETE ", "UPDATE ", "INSERT "):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
