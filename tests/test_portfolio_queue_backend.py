import tempfile
import unittest
from pathlib import Path

import server


class VpsPortfolioQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        # These tests intentionally simulate the legacy multi-worker pool; production remains one slot.
        self.original_global_limit = server.GLOBAL_CHATGPT_WORKER_LIMIT
        self.original_max_workers = server.MAX_CHATGPT_WORKERS
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 2
        server.MAX_CHATGPT_WORKERS = 2
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        root = Path(self.tmp.name)
        server.DB = root / "queue-test.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()

    def tearDown(self):
        server.GLOBAL_CHATGPT_WORKER_LIMIT = self.original_global_limit
        server.MAX_CHATGPT_WORKERS = self.original_max_workers
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

    def test_dynamic_worker_limit_reconciles_three_slots_immediately(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 1
        server.portfolio_queue_enqueue("cloud", "first", "P0", "prove first")
        server.portfolio_queue_enqueue("raiseai", "second", "P1", "prove second")
        server.portfolio_queue_enqueue("haxlab", "third", "P2", "prove third")

        settings = server.set_dynamic_worker_limit(3, "test-dashboard")

        self.assertEqual(3, settings["count"])
        self.assertTrue(settings["reconciled"])
        self.assertEqual(3, settings["allocated_workers"])
        self.assertEqual(3, len(server.global_worker_allocation()["workers"]))
        with server.connect() as conn:
            row = conn.execute(
                "SELECT value,actor FROM runtime_settings WHERE key=?",
                (server.DYNAMIC_WORKER_SETTING_KEY,),
            ).fetchone()
        self.assertEqual("3", row["value"])
        self.assertEqual("test-dashboard", row["actor"])

    def test_dynamic_worker_limit_downscale_releases_excess_slots(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.portfolio_queue_enqueue("cloud", "first", "P0", "prove first")
        server.portfolio_queue_enqueue("raiseai", "second", "P1", "prove second")
        server.portfolio_queue_enqueue("haxlab", "third", "P2", "prove third")
        server.portfolio_queue_allocate()
        server._persist_global_worker_allocation(server.global_worker_allocation())

        settings = server.set_dynamic_worker_limit(1, "test-dashboard")

        self.assertEqual(1, settings["allocated_workers"])
        with server.connect() as conn:
            excess = conn.execute(
                "SELECT COUNT(*) AS n FROM portfolio_queue WHERE worker_slot>1"
            ).fetchone()["n"]
            slots = conn.execute("SELECT COUNT(*) AS n FROM ai_global_slots").fetchone()["n"]
        self.assertEqual(0, excess)
        self.assertEqual(1, slots)

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
        continuation = result["next_task"]
        self.assertIsNotNone(continuation)
        self.assertEqual("P3", continuation["priority"])
        self.assertEqual("cloud", continuation["project_id"])

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
        self.assertEqual("backend-non-stopping-dynamic-worker-policy", row["reason"])

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
