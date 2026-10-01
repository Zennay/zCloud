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
        self.assertEqual("Execute substantial zCloud roadmap work package", continuation["title"])
        self.assertIn("at least three material implementation actions", continuation["completion_criteria"])
        self.assertIn("read-only inspection", continuation["completion_criteria"])

    def test_haxlab_done_creates_p3_write_first_continuation(self):
        item = server.portfolio_queue_enqueue("haxlab", "close evidence gate", "P2", "prove current gate")
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_finish(1, item["queue_id"], "DONE", "gate closed with current evidence")

        continuation = result["next_task"]
        self.assertIsNotNone(continuation)
        self.assertEqual("haxlab", continuation["project_id"])
        self.assertEqual("P3", continuation["priority"])
        self.assertEqual("Execute substantial HaxLab roadmap work package", continuation["title"])
        self.assertIn("adjacent write-capable steps", continuation["completion_criteria"])
        self.assertIn("read-only inspection", continuation["completion_criteria"])
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
        self.assertEqual("Execute substantial FTMO roadmap work package", continuation["title"])

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

    def test_drop_retires_obsolete_runtime_task_without_continuation(self):
        item = server.portfolio_queue_enqueue(
            "ftmo",
            "legacy runtime throughput task",
            "P0",
            "Implement runtime throughput recovery with deterministic tests.",
        )
        server.portfolio_queue_allocate()

        result = server.portfolio_queue_drop(
            item["queue_id"],
            "Superseded by persistent ftmo-autonomous-marathon.service.",
        )

        self.assertTrue(result["dropped"])
        row = {x["queue_id"]: x for x in server.portfolio_queue_items(True)}[item["queue_id"]]
        self.assertEqual("dropped", row["status"])
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["worker_slot"])

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

    def test_lane_generator_is_deterministic_and_uses_project_type(self):
        item = {
            "queue_id": "cloud-lane",
            "project_id": "cloud",
            "title": "Implement automatic worker lanes from queue backlog",
            "completion_criteria": "Implement scheduler claims and allocation tests.",
        }

        first = server.portfolio_lane_for_item(item)
        second = server.portfolio_lane_for_item(dict(item))

        self.assertEqual(first, second)
        self.assertEqual("infrastructure", first["project_type"])
        self.assertEqual("coordination", first["domain"])
        self.assertEqual(["cloud:infrastructure:coordination"], first["conflict_scope"]["capabilities"])
        self.assertEqual(["server.py"], first["conflict_scope"]["files"])

    def test_allocator_avoids_duplicate_conflicting_write_lanes(self):
        first = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler allocation",
            "P1",
            "Implement worker lane claims in server.py with regression tests.",
        )
        second = server.portfolio_queue_enqueue(
            "cloud",
            "Implement worker queue claim routing",
            "P1",
            "Implement scheduler allocation safeguards in server.py with tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual(1, len(selected))
        self.assertEqual(first["queue_id"], selected[0]["queue_id"])
        rows = {row["queue_id"]: row for row in server.portfolio_queue_items(True)}
        self.assertEqual("queued", rows[second["queue_id"]]["status"])
        self.assertIsNone(rows[second["queue_id"]]["worker_slot"])

    def test_allocator_allows_non_overlapping_lanes_in_same_project(self):
        coordination = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler allocation",
            "P1",
            "Implement worker lane claims in server.py with regression tests.",
        )
        delivery = server.portfolio_queue_enqueue(
            "cloud",
            "Implement deployment workflow release gate",
            "P1",
            "Update GitHub Actions deploy workflow and run deterministic tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual(2, len(selected))
        self.assertEqual(
            {coordination["queue_id"], delivery["queue_id"]},
            {row["queue_id"] for row in selected},
        )
        self.assertEqual(
            {"coordination", "delivery"},
            {row["execution_lane"]["domain"] for row in selected},
        )

    def test_active_task_claim_blocks_conflicting_backlog_lane(self):
        coordination = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler allocation",
            "P0",
            "Implement worker lane claims in server.py with regression tests.",
        )
        delivery = server.portfolio_queue_enqueue(
            "cloud",
            "Implement deployment workflow release gate",
            "P1",
            "Update GitHub Actions deploy workflow and run deterministic tests.",
        )
        lane = server.portfolio_lane_for_item(coordination)
        claimed = server.task_claim_acquire(
            "cloud",
            "external:coordination",
            "owner-external",
            "cloud::external",
            300,
            {"conflict_scope": lane["conflict_scope"]},
        )
        self.assertTrue(claimed["acquired"])

        lanes = server.portfolio_worker_lanes("cloud")
        blocked = {row["queue_id"]: row for row in lanes}
        self.assertFalse(blocked[coordination["queue_id"]]["safe"])
        self.assertEqual("task_claim", blocked[coordination["queue_id"]]["blocked_by"]["kind"])
        self.assertTrue(blocked[delivery["queue_id"]]["safe"])

        selected = server.portfolio_queue_allocate()
        self.assertEqual([delivery["queue_id"]], [row["queue_id"] for row in selected])

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
