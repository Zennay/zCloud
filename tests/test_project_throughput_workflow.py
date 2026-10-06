from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-throughput.yml"


class ProjectThroughputWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_read_only_permanent_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn("ZCLOUD_PROJECT_THROUGHPUT_READONLY_GREEN", text)
        self.assertIn("before_fingerprint=", text)
        self.assertIn("after_fingerprint=", text)
        self.assertIn(
            "group: zcloud-project-throughput-${{ github.event.pull_request.head.ref || github.ref_name }}",
            text,
        )
        self.assertIn("cancel-in-progress: true", text)

    def test_workflow_never_mutates_live_queue_or_receipts(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for forbidden in (
            "--apply",
            "sudo ",
            "systemctl ",
            "INSERT INTO ",
            "UPDATE portfolio_queue",
            "DELETE FROM portfolio_queue",
            "portfolio_queue_finish",
            "record_receipt(",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
