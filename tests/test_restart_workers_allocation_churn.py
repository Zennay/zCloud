import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersAllocationChurnTests(unittest.TestCase):
    def test_superseded_assignment_does_not_fail_restart(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"queue_id": str(row.get("queue_id") or "")', text)
        self.assertIn('current_status, current_targets = api_call("GET", "/api/runner-targets")', text)
        self.assertIn("superseded_workers = []", text)
        self.assertIn('str(current.get("queue_id") or "") != str(original.get("queue_id") or "")', text)
        self.assertIn("and worker not in superseded_workers", text)

    def test_allocation_churn_pushes_replacement_workers(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("allocation_changed = set(current_by_key) != set(queued_by_key)", text)
        self.assertIn('api_call(\n                  "POST", "/api/dynamic-workers/force-push", {}', text)
        self.assertIn('"replacement_push": replacement_push', text)


if __name__ == "__main__":
    unittest.main()
