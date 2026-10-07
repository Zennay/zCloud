from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-actions-queue-pressure.yml"


class ActionsQueuePressureWorkflowTests(unittest.TestCase):
    def test_workflow_is_hosted_read_only_and_exact_head_bound(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("runs-on: self-hosted", text)
        self.assertNotIn("[self-hosted", text)
        self.assertIn("contents: read", text)
        self.assertIn("actions: read", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)

    def test_workflow_only_reads_actions_and_runs_bounded_snapshot(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('/actions/runs?status={status}&per_page=100', text)
        self.assertIn('active_statuses = ("queued", "in_progress", "waiting", "pending", "requested")', text)
        self.assertIn('int(payload.get("total_count") or 0) > len(rows)', text)
        self.assertIn("active = active[:30]", text)
        self.assertIn("/jobs?per_page=100", text)
        self.assertIn("jobs[:100]", text)
        self.assertIn('"capacity": 1', text)
        self.assertNotIn("--require-complete", text)
        self.assertNotIn("/cancel", text)
        self.assertNotIn("/rerun", text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("sqlite", text.lower())
        self.assertNotIn("POST", text)
        self.assertNotIn("PUT", text)
        self.assertNotIn("PATCH", text)
        self.assertNotIn("DELETE", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
