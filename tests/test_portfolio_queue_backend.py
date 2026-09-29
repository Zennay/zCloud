import tempfile
import unittest
from pathlib import Path

import server


class VpsPortfolioQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        root = Path(self.tmp.name)
        server.DB = root / "queue-test.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()

    def tearDown(self):
        server.DB = self.original_db
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_seed
        self.tmp.cleanup()

    def test_claims_priority_and_two_global_slots(self):
        server.portfolio_queue_enqueue("haxlab", "normal", "P2", "prove normal")
        server.portfolio_queue_enqueue("raiseai", "high", "P1", "prove high")
        server.portfolio_queue_enqueue("cloud", "critical", "P0", "prove critical")

        selected = server.portfolio_queue_allocate()

        self.assertEqual(["P0", "P1"], [item["priority"] for item in selected])
        self.assertEqual([1, 2], [item["worker_slot"] for item in selected])
        allocation = server.global_worker_allocation()
        self.assertEqual("sqlite", allocation["queue_backend"])
        self.assertEqual(["cloud", "raiseai"], [worker["project_id"] for worker in allocation["workers"]])

    def test_done_releases_slot_and_next_item_is_claimed(self):
        first = server.portfolio_queue_enqueue("cloud", "first", "P0", "prove first")
        second = server.portfolio_queue_enqueue("haxlab", "second", "P1", "prove second")
        third = server.portfolio_queue_enqueue("raiseai", "third", "P2", "prove third")
        server.portfolio_queue_allocate()
        self.assertEqual(second["queue_id"], server.portfolio_queue_current_for_slot(2)["queue_id"])

        result = server.portfolio_queue_finish(1, first["queue_id"], "DONE", "commit abc; tests green")
        self.assertTrue(result["updated"])

        server.portfolio_queue_allocate()
        current = server.portfolio_queue_current_for_slot(1)
        self.assertEqual(third["queue_id"], current["queue_id"])
        self.assertEqual("claimed", current["status"])
        done = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}[first["queue_id"]]
        self.assertEqual("done", done["status"])
        self.assertFalse(done["eligible"])

    def test_backend_rejects_complete_while_other_queue_work_exists(self):
        first = server.portfolio_queue_enqueue("cloud", "first", "P0", "prove first")
        server.portfolio_queue_enqueue("raiseai", "second", "P1", "prove second")
        server.portfolio_queue_allocate()

        server.runner_record({
            "event": "autonomy-complete",
            "projectId": "cloud::w1",
            "baseProjectId": "cloud",
            "workerSlot": 1,
            "globalWorkerSlot": 1,
            "queueItem": first["queue_id"],
            "generating": False,
            "sending": False,
        })
        with server.connect() as conn:
            row = conn.execute(
                "SELECT event,reason FROM runner_events WHERE project_id='cloud' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual("autonomy-continue", row["event"])
        self.assertEqual("backend-rejected-terminal-signal-queue-not-empty", row["reason"])

    def test_continue_requeues_without_notion_dependency(self):
        item = server.portfolio_queue_enqueue("raiseai", "iterate", "P1", "prove next state")
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_finish(1, item["queue_id"], "CONTINUE", "state advanced")
        self.assertTrue(result["updated"])
        queued = {row["queue_id"]: row for row in server.portfolio_queue_items(True)}[item["queue_id"]]
        self.assertEqual("queued", queued["status"])
        self.assertTrue(queued["eligible"])
        self.assertIsNone(queued["worker_slot"])

        server.portfolio_queue_allocate()
        reclaimed = server.portfolio_queue_current_for_slot(1)
        self.assertEqual(item["queue_id"], reclaimed["queue_id"])


if __name__ == "__main__":
    unittest.main()
