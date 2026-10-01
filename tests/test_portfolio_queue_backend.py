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
        self.original_chatgpt_workers = server.DYNAMIC_CHATGPT_WORKERS
        self.original_claude_workers = server.DYNAMIC_CLAUDE_WORKERS
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 2
        server.MAX_CHATGPT_WORKERS = 2
        server.DYNAMIC_CHATGPT_WORKERS = 2
        server.DYNAMIC_CLAUDE_WORKERS = 0
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        root = Path(self.tmp.name)
        server.DB = root / "queue-test.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()

    def tearDown(self):
        server.GLOBAL_CHATGPT_WORKER_LIMIT = self.original_global_limit
        server.MAX_CHATGPT_WORKERS = self.original_max_workers
        server.DYNAMIC_CHATGPT_WORKERS = self.original_chatgpt_workers
        server.DYNAMIC_CLAUDE_WORKERS = self.original_claude_workers
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

    def test_p0_preempts_lower_priority_claim_that_has_not_started(self):
        lower = server.portfolio_queue_enqueue("cloud", "lower", "P1", "Implement lower-priority change with tests.")
        server.portfolio_queue_allocate()
        self.assertEqual(lower["queue_id"], server.portfolio_queue_current_for_slot(1)["queue_id"])

        higher = server.portfolio_queue_enqueue("ftmo", "urgent recovery", "P0", "Implement FTMO recovery with deterministic tests.")
        selected = server.portfolio_queue_allocate()

        self.assertEqual(higher["queue_id"], selected[0]["queue_id"])
        rows = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}
        self.assertEqual("queued", rows[lower["queue_id"]]["status"])
        self.assertIsNone(rows[lower["queue_id"]]["worker_slot"])

    def test_p0_does_not_preempt_running_lower_priority_work(self):
        lower = server.portfolio_queue_enqueue("cloud", "running lower", "P1", "Implement lower-priority change with tests.")
        server.portfolio_queue_allocate()
        with server.connect() as conn:
            conn.execute("UPDATE portfolio_queue SET status='running' WHERE queue_id=?", (lower["queue_id"],))

        server.portfolio_queue_enqueue("ftmo", "urgent recovery", "P0", "Implement FTMO recovery with deterministic tests.")
        selected = server.portfolio_queue_allocate()

        self.assertEqual(lower["queue_id"], selected[0]["queue_id"])
        self.assertEqual("running", selected[0]["status"])

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
        second = server.portfolio_queue_enqueue("raiseai", "second", "P1", "prove second")
        third = server.portfolio_queue_enqueue("supa", "third", "P2", "prove third")
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
        self.assertEqual("P2", continuation["priority"])
        self.assertEqual("cloud", continuation["project_id"])
        self.assertIn("Implement next zCloud roadmap increment", continuation["title"])
        self.assertIn("material code/config/workflow/experiment/runtime state change", continuation["completion_criteria"])
        self.assertIn("Read-only inspection", continuation["completion_criteria"])

    def test_haxlab_done_creates_p3_write_first_continuation(self):
        item = server.portfolio_queue_enqueue("haxlab", "close evidence gate", "P2", "prove current gate")
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_finish(1, item["queue_id"], "DONE", "gate closed with current evidence")

        continuation = result["next_task"]
        self.assertIsNotNone(continuation)
        self.assertEqual("haxlab", continuation["project_id"])
        self.assertEqual("P3", continuation["priority"])
        self.assertEqual("Implement next HaxLab roadmap increment", continuation["title"])
        self.assertIn("write-capable roadmap step", continuation["completion_criteria"])
        self.assertIn("Read-only inspection", continuation["completion_criteria"])
        self.assertIn(server.PROJECT_INDEX["haxlab"]["next_step"], continuation["completion_criteria"])
        self.assertEqual(server.PROJECT_INDEX["haxlab"]["notion_url"], continuation["source_url"])

    def test_ftmo_done_creates_p0_write_first_continuation(self):
        item = server.portfolio_queue_enqueue("ftmo", "advance research gate", "P1", "Implement the next safe FTMO gate with tests.")
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_finish(1, item["queue_id"], "DONE", "gate advanced with current evidence")

        continuation = result["next_task"]
        self.assertIsNotNone(continuation)
        self.assertEqual("ftmo", continuation["project_id"])
        self.assertEqual("P0", continuation["priority"])
        self.assertEqual("Implement next FTMO roadmap increment", continuation["title"])

    def test_haxlab_explicit_implementation_priority_is_preserved(self):
        p1 = server.portfolio_queue_enqueue(
            "haxlab",
            "Implement Arena v2 increment",
            "P1",
            "Implement code plus deterministic tests.",
        )
        p2 = server.portfolio_queue_enqueue(
            "haxlab",
            "Implement rollout recovery increment",
            "P2",
            "Implement code plus deterministic tests.",
        )
        self.assertEqual("P1", p1["priority"])
        self.assertEqual("P2", p2["priority"])

    def test_haxlab_cannot_enter_p0_system_tier(self):
        item = server.portfolio_queue_enqueue(
            "haxlab",
            "Implement bounded HaxLab improvement",
            "P0",
            "Implement code plus deterministic tests.",
        )
        self.assertEqual("P2", item["priority"])

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

    def test_queue_rejects_read_only_status_work(self):
        with self.assertRaisesRegex(ValueError, "uitvoerbare write/build/test/deploy"):
            server.portfolio_queue_enqueue(
                "cloud",
                "Inspect current deployment status",
                "P1",
                "Read-only verification of current VPS state; record evidence only.",
            )

    def test_blocked_human_gate_moves_to_attention_and_leaves_queue(self):
        item = server.portfolio_queue_enqueue(
            "raiseai",
            "Implement physical-device handoff",
            "P1",
            "Implement the code/config handoff and tests for the device step.",
        )
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_finish(
            1,
            item["queue_id"],
            "BLOCKED",
            "Physical Watch test requires human action on the paired device.",
        )

        rows = {row["queue_id"]: row for row in server.portfolio_queue_items(True)}
        self.assertEqual("dropped", rows[item["queue_id"]]["status"])
        self.assertFalse(rows[item["queue_id"]]["eligible"])
        attention = server.portfolio_attention_items()
        self.assertTrue(any(row["project_id"] == "raiseai" for row in attention))
        self.assertIsNotNone(result["attention"])

    def test_human_gated_project_never_enters_worker_queue(self):
        with self.assertRaisesRegex(ValueError, "human-gated"):
            server.portfolio_queue_enqueue(
                "ulab",
                "Implement another M5 preparation task",
                "P2",
                "Implement code and tests.",
            )
        audit = server.portfolio_queue_audit()
        active_projects = {row["project_id"] for row in server.portfolio_queue_items()}
        self.assertNotIn("ulab", active_projects)
        self.assertGreaterEqual(audit["projects"], 1)

    def test_queue_audit_refills_broad_execution_work(self):
        audit = server.portfolio_queue_audit()
        projects = {row["project_id"] for row in server.portfolio_queue_items()}
        self.assertIn("cloud", projects)
        self.assertIn("haxlab", projects)
        self.assertIn("ftmo", projects)
        self.assertIn("supa", projects)
        self.assertIn("raiseai", projects)
        self.assertIn("zssh", projects)
        self.assertNotIn("ulab", projects)
        self.assertGreaterEqual(audit["ready"], 6)


if __name__ == "__main__":
    unittest.main()
