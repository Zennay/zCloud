from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-activity-event-grouping-proof.yml"


class ActivityEventGroupingWorkflowTests(unittest.TestCase):
    def test_workflow_is_permanent_vps_temporary_state_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("ZCLOUD_ACTIVITY_EVENT_GROUPING_TEMPSTATE_GREEN", text)
        self.assertNotIn("/home/ubuntu/zennay-cloud/history.db", text)
        for forbidden in ("sudo ", "systemctl ", "--apply", "DELETE ", "UPDATE ", "INSERT "):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
