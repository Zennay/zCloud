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
        self.original_worker_memory_status = server.worker_memory_status
        server.worker_memory_status = lambda *args, **kwargs: {
            "available_mb": 16384,
            "total_mb": 32768,
            "swap_total_mb": 4096,
            "swap_free_mb": 4096,
            "headroom_mb": 2048,
            "effective_headroom_mb": 2048,
            "per_new_slot_mb": 1536,
            "new_worker_capacity": 8,
            "pressure": "ok",
            "healthy_for_new_worker": True,
            "swap_healthy": True,
        }
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
        server.worker_memory_status = self.original_worker_memory_status
        server.DB = self.original_db
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_seed
        self.tmp.cleanup()

    def test_connect_uses_bounded_busy_timeout(self):
        with server.connect() as conn:
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(server.DB_BUSY_TIMEOUT_MS, busy_timeout)
        self.assertGreaterEqual(busy_timeout, 15000)

    def test_init_db_clamps_persisted_ftmo_worker_count_to_two(self):
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET worker_count=5 WHERE project_id='ftmo'")

        server.init_db()

        with server.connect() as conn:
            worker_count = conn.execute(
                "SELECT worker_count FROM runner_targets WHERE project_id='ftmo'"
            ).fetchone()[0]
        self.assertEqual(2, worker_count)

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

    def test_external_gate_keeps_queue_queued_and_releases_unstarted_claim(self):
        blocked = server.portfolio_queue_enqueue(
            "zssh",
            "Implement gated zSSH release step",
            "P0",
            "Implement the next zSSH release step with deterministic tests.",
        )
        runnable = server.portfolio_queue_enqueue(
            "raiseai",
            "Implement runnable gateway step",
            "P1",
            "Implement the next Raise AI gateway step with deterministic tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual([runnable["queue_id"]], [item["queue_id"] for item in selected])
        blocked_row = next(
            item for item in server.portfolio_queue_items(True)
            if item["queue_id"] == blocked["queue_id"]
        )
        self.assertEqual("queued", blocked_row["status"])
        self.assertIsNone(blocked_row["worker_slot"])

        with server.connect() as conn:
            conn.execute(
                "UPDATE portfolio_queue SET status='claimed',worker_slot=2,claim_expires=NULL WHERE queue_id=?",
                (blocked["queue_id"],),
            )
        self.assertTrue(server.portfolio_queue_has_project_assignment("zssh"))
        self.assertFalse(server.runner_targets()["zssh"]["auto_continue"])

        selected = server.portfolio_queue_allocate()

        blocked_row = next(
            item for item in server.portfolio_queue_items(True)
            if item["queue_id"] == blocked["queue_id"]
        )
        self.assertEqual("queued", blocked_row["status"])
        self.assertIsNone(blocked_row["worker_slot"])
        self.assertNotIn(blocked["queue_id"], [item["queue_id"] for item in selected])


    def test_external_gate_preserves_running_and_verifying_work_without_auto_continue(self):
        running = server.portfolio_queue_enqueue(
            "zssh",
            "Finish running zSSH release evidence",
            "P0",
            "Complete the already-running zSSH release evidence safely.",
        )
        verifying = server.portfolio_queue_enqueue(
            "zssh",
            "Verify active zSSH release evidence",
            "P0",
            "Verify the already-started zSSH release evidence safely.",
        )
        with server.connect() as conn:
            conn.execute(
                "UPDATE portfolio_queue SET status='running',worker_slot=1,claim_expires=NULL WHERE queue_id=?",
                (running["queue_id"],),
            )
            conn.execute(
                "UPDATE portfolio_queue SET status='verifying',worker_slot=2,claim_expires=NULL WHERE queue_id=?",
                (verifying["queue_id"],),
            )

        selected = server.portfolio_queue_allocate()

        selected_by_id = {item["queue_id"]: item for item in selected}
        self.assertEqual("running", selected_by_id[running["queue_id"]]["status"])
        self.assertEqual("verifying", selected_by_id[verifying["queue_id"]]["status"])
        self.assertFalse(server.runner_targets()["zssh"]["auto_continue"])
        rows = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}
        self.assertEqual(1, rows[running["queue_id"]]["worker_slot"])
        self.assertEqual(2, rows[verifying["queue_id"]]["worker_slot"])

    def test_three_slots_keep_one_slot_for_another_runnable_project(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.portfolio_queue_enqueue(
            "ftmo", "Implement strategy generation critical path", "P0",
            "Implement the next strategy generation step with deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "ftmo", "Validate walk-forward gate", "P0",
            "Implement walk-forward validation and deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "ftmo", "Harden provider data provenance", "P0",
            "Implement provider provenance checks with deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "raiseai", "Implement runner deploy recovery", "P2",
            "Implement deploy recovery with deterministic tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual(3, len(selected))
        self.assertEqual(["ftmo", "ftmo", "raiseai"], [item["project_id"] for item in selected])

    def test_ftmo_hard_cap_keeps_third_slot_unallocated_without_alternative(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.portfolio_queue_enqueue(
            "ftmo", "Implement strategy generation critical path", "P0",
            "Implement the next strategy generation step with deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "ftmo", "Validate walk-forward gate", "P0",
            "Implement walk-forward validation and deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "ftmo", "Harden provider data provenance", "P0",
            "Implement provider provenance checks with deterministic tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual(2, len(selected))
        self.assertEqual(["ftmo", "ftmo"], [item["project_id"] for item in selected])
        queued_ftmo = [
            item for item in server.portfolio_queue_items(True)
            if item["project_id"] == "ftmo" and item["status"] == "queued"
        ]
        self.assertEqual(1, len(queued_ftmo))
        self.assertIsNone(queued_ftmo[0]["worker_slot"])

    def test_memory_guard_blocks_new_claims_but_preserves_existing_assignment(self):
        first = server.portfolio_queue_enqueue(
            "cloud", "first", "P0", "Implement first guarded worker task with tests."
        )
        server.portfolio_queue_allocate()
        self.assertEqual(first["queue_id"], server.portfolio_queue_current_for_slot(1)["queue_id"])

        server.portfolio_queue_enqueue(
            "raiseai", "second", "P1", "Implement second guarded worker task with tests."
        )
        server.worker_memory_status = lambda *args, **kwargs: {
            "available_mb": 900,
            "total_mb": 11000,
            "swap_total_mb": 0,
            "swap_free_mb": 0,
            "headroom_mb": 2048,
            "effective_headroom_mb": 2560,
            "per_new_slot_mb": 1536,
            "new_worker_capacity": 0,
            "pressure": "critical",
            "healthy_for_new_worker": False,
            "swap_healthy": False,
        }

        selected = server.portfolio_queue_allocate()

        self.assertEqual(1, len(selected))
        self.assertEqual(first["queue_id"], selected[0]["queue_id"])
        self.assertIsNone(server.portfolio_queue_current_for_slot(2))

    def test_oom_recovery_trim_holds_excess_slots_queued(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.portfolio_queue_enqueue(
            "cloud", "first recovery lane", "P0",
            "Implement first recovery lane with deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "raiseai", "second recovery lane", "P1",
            "Implement second recovery lane with deterministic tests.",
        )
        server.portfolio_queue_enqueue(
            "haxlab", "third recovery lane", "P2",
            "Implement third recovery lane with deterministic tests.",
        )
        server.portfolio_queue_allocate()
        server._persist_global_worker_allocation(server.portfolio_queue_allocation())
        self.assertEqual(3, len(server.portfolio_queue_allocation()["workers"]))

        server.worker_memory_status = lambda *args, **kwargs: {
            "available_mb": 3600,
            "total_mb": 11264,
            "swap_total_mb": 0,
            "swap_free_mb": 0,
            "headroom_mb": 2048,
            "effective_headroom_mb": 2560,
            "per_new_slot_mb": 1536,
            "new_worker_capacity": 1,
            "pressure": "ok",
            "healthy_for_new_worker": True,
            "swap_healthy": False,
        }
        released = server._trim_dead_browser_allocations_for_recovery(1)
        server._set_worker_recovery_hold(60)
        server._persist_global_worker_allocation(server.portfolio_queue_allocation())

        selected = server.portfolio_queue_allocate()

        self.assertEqual(2, len(released))
        self.assertEqual(1, len(selected))
        self.assertEqual(1, len(server.portfolio_queue_allocation()["workers"]))
        queued = [
            item for item in server.portfolio_queue_items(True)
            if item["status"] == "queued"
        ]
        self.assertGreaterEqual(len(queued), 2)

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

    def test_allocator_assigns_distinct_ftmo_lanes_and_persists_metadata(self):
        server.MAX_CHATGPT_WORKERS = 3
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.DYNAMIC_CHATGPT_WORKERS = 3
        server.portfolio_queue_enqueue(
            "ftmo",
            "Implement next generation candidate",
            "P1",
            "Implement the next candidate and deterministic backtest evidence.",
        )
        server.portfolio_queue_enqueue(
            "ftmo",
            "Run frozen walk-forward validation",
            "P1",
            "Implement the validation harness and run deterministic walk-forward tests.",
        )
        server.portfolio_queue_enqueue(
            "ftmo",
            "Repair provider data provenance",
            "P1",
            "Implement provider provenance safeguards and deterministic tests.",
        )

        selected = server.portfolio_queue_allocate()

        self.assertEqual(2, len(selected))
        self.assertEqual({"ftmo"}, {item["project_id"] for item in selected})
        lanes = {item["execution_lane"]["lane_id"] for item in selected}
        self.assertEqual({"critical-path", "qa-validation"}, lanes)
        for item in selected:
            self.assertTrue(item["execution_lane"]["scope"]["capabilities"])

    def test_allocator_skips_lane_blocked_by_active_task_claim(self):
        blocked = server.portfolio_queue_enqueue(
            "ftmo",
            "Repair provider data provenance",
            "P0",
            "Implement provider provenance safeguards and deterministic tests.",
        )
        fallback = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler guard",
            "P1",
            "Implement allocator safeguards with deterministic tests.",
        )
        claim = server.task_claim_acquire(
            "ftmo",
            "provider:data-repair",
            "other-owner",
            "ftmo::w9",
            120,
            {
                "conflict_scope": {
                    "capabilities": ["ftmo-data-provenance"],
                    "files": [],
                }
            },
        )
        self.assertTrue(claim["acquired"])

        selected = server.portfolio_queue_allocate()
        selected_ids = {item["queue_id"] for item in selected}

        self.assertNotIn(blocked["queue_id"], selected_ids)
        self.assertIn(fallback["queue_id"], selected_ids)
        lanes = server.portfolio_execution_lanes("ftmo")
        data_lane = next(lane for lane in lanes if lane["lane_id"] == "data-provenance")
        self.assertEqual("blocked", data_lane["status"])
        self.assertEqual("provider:data-repair", data_lane["blocked_by"][0]["claim_key"])

    def test_allocator_does_not_assign_cross_lane_items_with_same_file_scope(self):
        control = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler lane allocation",
            "P1",
            "Implement queue scheduling with deterministic tests.",
        )
        deploy = server.portfolio_queue_enqueue(
            "cloud",
            "Harden VPS deploy workflow",
            "P2",
            "Implement deployment hardening with deterministic tests.",
        )
        scope = '{"conflict_scope":{"capabilities":[],"files":["server.py"]}}'
        with server.connect() as conn:
            conn.execute(
                "UPDATE portfolio_queue SET metadata_json=? WHERE queue_id IN (?,?)",
                (scope, control["queue_id"], deploy["queue_id"]),
            )

        selected = server.portfolio_queue_allocate()

        self.assertEqual([control["queue_id"]], [item["queue_id"] for item in selected])
        rows = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}
        self.assertEqual("claimed", rows[control["queue_id"]]["status"])
        self.assertEqual("queued", rows[deploy["queue_id"]]["status"])
        self.assertIsNone(rows[deploy["queue_id"]]["worker_slot"])
        lanes = server.portfolio_execution_lanes("cloud")
        deploy_lane = next(lane for lane in lanes if lane["lane_id"] == "deploy-ops")
        self.assertEqual("blocked", deploy_lane["status"])
        self.assertEqual("queue", deploy_lane["blocked_by"][0]["kind"])
        self.assertEqual(control["queue_id"], deploy_lane["blocked_by"][0]["queue_id"])

    def test_allocator_requeues_duplicate_unstarted_claimed_lane(self):
        first = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler lane allocation",
            "P1",
            "Implement queue scheduling with deterministic tests.",
        )
        second = server.portfolio_queue_enqueue(
            "cloud",
            "Implement worker queue claim routing",
            "P2",
            "Implement worker claim routing with deterministic tests.",
        )
        with server.connect() as conn:
            conn.execute(
                """UPDATE portfolio_queue
                   SET status='claimed',worker_slot=1,claimed_at=?,claim_expires=?
                   WHERE queue_id=?""",
                ("2026-10-01T23:00:00+00:00", "2099-01-01T00:00:00+00:00", first["queue_id"]),
            )
            conn.execute(
                """UPDATE portfolio_queue
                   SET status='claimed',worker_slot=2,claimed_at=?,claim_expires=?
                   WHERE queue_id=?""",
                ("2026-10-01T23:01:00+00:00", "2099-01-01T00:00:00+00:00", second["queue_id"]),
            )

        selected = server.portfolio_queue_allocate()

        self.assertEqual([first["queue_id"]], [item["queue_id"] for item in selected])
        rows = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}
        self.assertEqual("queued", rows[second["queue_id"]]["status"])
        self.assertIsNone(rows[second["queue_id"]]["worker_slot"])

    def test_running_lane_forces_conflicting_claimed_file_scope_back_to_queue(self):
        running = server.portfolio_queue_enqueue(
            "cloud",
            "Implement queue scheduler lane allocation",
            "P2",
            "Implement queue scheduling with deterministic tests.",
        )
        waiting = server.portfolio_queue_enqueue(
            "cloud",
            "Harden VPS deploy workflow",
            "P0",
            "Implement deployment hardening with deterministic tests.",
        )
        scope = '{"conflict_scope":{"capabilities":[],"files":["server.py"]}}'
        with server.connect() as conn:
            conn.execute(
                """UPDATE portfolio_queue
                   SET status='running',worker_slot=1,claimed_at=?,claim_expires=?,metadata_json=?
                   WHERE queue_id=?""",
                ("2026-10-01T23:00:00+00:00", "2099-01-01T00:00:00+00:00", scope, running["queue_id"]),
            )
            conn.execute(
                """UPDATE portfolio_queue
                   SET status='claimed',worker_slot=2,claimed_at=?,claim_expires=?,metadata_json=?
                   WHERE queue_id=?""",
                ("2026-10-01T23:01:00+00:00", "2099-01-01T00:00:00+00:00", scope, waiting["queue_id"]),
            )

        selected = server.portfolio_queue_allocate()

        self.assertEqual([running["queue_id"]], [item["queue_id"] for item in selected])
        rows = {item["queue_id"]: item for item in server.portfolio_queue_items(True)}
        self.assertEqual("running", rows[running["queue_id"]]["status"])
        self.assertEqual("queued", rows[waiting["queue_id"]]["status"])
        self.assertIsNone(rows[waiting["queue_id"]]["worker_slot"])

    def test_worker_prompt_contains_persisted_execution_lane(self):
        server.portfolio_queue_enqueue(
            "cloud",
            "Implement automatic queue lanes",
            "P1",
            "Implement queue lane scheduling with deterministic regression tests.",
        )
        item = server.portfolio_queue_allocate()[0]

        prompt = server.project_worker_prompt(
            "cloud",
            "zCloud",
            "",
            1,
            1,
            item,
        )

        self.assertEqual("control-plane", item["execution_lane"]["lane_id"])
        self.assertTrue(item["execution_lane"]["scope"]["capabilities"])
        self.assertEqual(server.project_runner_prompt("cloud", "zCloud"), prompt)
        self.assertNotIn("control-plane", prompt)
        self.assertNotIn("VPS_QUEUE_ASSIGNMENT", prompt)


    def test_zssh_continuation_is_research_first_and_ship_ready(self):
        item = server.portfolio_write_continuation("zssh", parent_queue_id="test-zssh-research-first")

        self.assertIsNotNone(item)
        criteria = item["completion_criteria"]
        self.assertIn("zSSH research-first rule", criteria)
        self.assertIn("current primary sources", criteria)
        self.assertIn("simple connect website", criteria)
        self.assertIn("scoped sudo grants", criteria)
        self.assertIn("submission-readiness evidence", criteria)


if __name__ == "__main__":
    unittest.main()
