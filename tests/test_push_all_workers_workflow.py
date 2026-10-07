from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PushAllWorkersWorkflowTests(unittest.TestCase):
    def test_push_all_requires_material_browser_progress(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('deadline = time.time() + 60', text)
        self.assertIn('progressed = set()', text)
        self.assertIn('if state.get("status") != "pending":', text)
        self.assertIn('live = call("GET", "/api/runner-live")', text)
        self.assertIn('not runtime["generating"] and not runtime["sending"]', text)
        self.assertIn('fresh push stayed pending without active generation/sending', text)
        self.assertIn('MATERIAL_PUSH_PROGRESS=', text)
        self.assertIn('ALL_PENDING_JUSTIFIED_BY_ACTIVE_GENERATION=1', text)
        self.assertIn('ALL_WORKERS_PUSHED_GREEN=1', text)

    def test_push_all_keeps_failure_and_missing_rows_fail_closed(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('fresh push failed for:', text)
        self.assertIn('fresh push command missing for:', text)
        self.assertIn('not all allocated workers were queued:', text)


if __name__ == "__main__":
    unittest.main()
