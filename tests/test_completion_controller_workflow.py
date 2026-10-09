import tempfile
import unittest
from pathlib import Path

import completion_controller as cc
import server


class CompletionControllerWorkflowTests(unittest.TestCase):
    """Server-level check that the portfolio queue refill prefers finishing
    an already-open PR over handing a project a brand new roadmap package —
    the core behaviour change the Worker Completion Controller exists for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        root = Path(self.tmp.name)
        server.DB = root / "completion-test.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()
        # zguard is a real, non-human-gated project in the shipped registry
        # with no special-cased priority rules, which keeps this test
        # focused on the completion-first behaviour rather than priority math.
        self.project_id = "zguard"

    def tearDown(self):
        server.DB = self.original_db
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_seed
        self.tmp.cleanup()

    def _conflicting_pr(self, number=250):
        pr = {
            "title": "Receipt backfill",
            "html_url": f"https://github.com/Zennay/zGuard/pull/{number}",
            "mergeable_state": "dirty",
            "draft": False,
            "updated_at": server.now(),
        }
        classification = cc.classify_pr(pr, [])
        with server.connect() as c:
            cc.record_pr_state(c, self.project_id, "zGuard", number, pr, classification)
        return pr, classification

    def test_audit_enqueues_completion_item_when_unfinished_pr_exists(self):
        self._conflicting_pr(250)
        result = server.portfolio_queue_audit(refill=True)
        self.assertTrue(any(q.startswith(self.project_id + "-") for q in result["created"]))
        items = server.portfolio_queue_items()
        mine = [item for item in items if item["project_id"] == self.project_id]
        self.assertEqual(len(mine), 1)
        self.assertIn("#250", mine[0]["title"])
        self.assertIn("250", mine[0]["completion_criteria"])
        self.assertNotIn("Execute substantial", mine[0]["title"])

    def test_audit_falls_back_to_generic_continuation_without_unfinished_work(self):
        result = server.portfolio_queue_audit(refill=True)
        self.assertTrue(any(q.startswith(self.project_id + "-") for q in result["created"]))
        items = server.portfolio_queue_items()
        mine = [item for item in items if item["project_id"] == self.project_id]
        self.assertEqual(len(mine), 1)
        self.assertIn("Execute substantial", mine[0]["title"])

    def test_completion_first_continuation_records_task_started_metric(self):
        self._conflicting_pr(251)
        server.portfolio_completion_first_continuation(self.project_id)
        with server.connect() as c:
            rate = cc.material_completion_rate(c, self.project_id)
        self.assertEqual(rate["tasks_started"], 1)

    def test_mergeable_pr_is_not_treated_as_unfinished_work(self):
        pr = {
            "title": "Trivial fix", "html_url": "https://github.com/Zennay/zGuard/pull/252",
            "mergeable_state": "clean", "draft": False, "updated_at": server.now(),
        }
        classification = cc.classify_pr(pr, [{"status": "completed", "conclusion": "success"}])
        with server.connect() as c:
            cc.record_pr_state(c, self.project_id, "zGuard", 252, pr, classification)
        item = server.portfolio_completion_first_continuation(self.project_id)
        self.assertIsNone(item)


if __name__ == "__main__":
    unittest.main()
