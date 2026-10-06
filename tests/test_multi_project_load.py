import collections
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import server


class MultiProjectControlPlaneLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-multiproject-load-")
        self.saved = {
            "DB": server.DB,
            "PORTFOLIO_QUEUE_SEED_FILE": server.PORTFOLIO_QUEUE_SEED_FILE,
            "GLOBAL_CHATGPT_WORKER_LIMIT": server.GLOBAL_CHATGPT_WORKER_LIMIT,
            "MAX_CHATGPT_WORKERS": server.MAX_CHATGPT_WORKERS,
            "DYNAMIC_CHATGPT_WORKERS": server.DYNAMIC_CHATGPT_WORKERS,
            "DYNAMIC_CLAUDE_WORKERS": server.DYNAMIC_CLAUDE_WORKERS,
            "worker_memory_status": server.worker_memory_status,
        }
        root = Path(self.tmp.name)
        server.DB = root / "load.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]\n", encoding="utf-8")
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 8
        server.MAX_CHATGPT_WORKERS = 8
        server.DYNAMIC_CHATGPT_WORKERS = 6
        server.DYNAMIC_CLAUDE_WORKERS = 2
        server.worker_memory_status = lambda *args, **kwargs: {
            "available_mb": 24576,
            "total_mb": 32768,
            "swap_total_mb": 4096,
            "swap_free_mb": 4096,
            "headroom_mb": 2048,
            "effective_headroom_mb": 2048,
            "per_new_slot_mb": 1536,
            "new_worker_capacity": 16,
            "pressure": "ok",
            "healthy_for_new_worker": True,
            "swap_healthy": True,
        }
        server.init_db()
        with server.connect() as conn:
            conn.execute("DELETE FROM portfolio_queue")
            conn.execute("DELETE FROM ai_global_slots")
            conn.execute("DELETE FROM runner_workers")

        self.projects = {
            "lightup": {
                "priority": "P1",
                "cap": 1,
                "tasks": [
                    ("Implement scope authorization guard", "Implement authorization scope permission guardrails."),
                ],
            },
            "zguard": {
                "priority": "P1",
                "cap": 1,
                "tasks": [
                    ("Implement product UI state", "Implement product UI state flow with tests."),
                ],
            },
            "cloud": {
                "priority": "P1",
                "cap": 1,
                "tasks": [
                    ("Implement queue allocator state", "Implement control-plane queue allocator state with tests."),
                ],
            },
            "ftmo": {
                "priority": "P0",
                "cap": 2,
                "tasks": [
                    ("Implement strategy generation candidate", "Implement research critical-path candidate generation."),
                    ("Implement walk-forward validation gate", "Implement QA validation walk-forward tests."),
                    ("Implement provider data provenance", "Implement data provider provenance checks."),
                ],
            },
            "supa": {
                "priority": "P2",
                "cap": 2,
                "tasks": [
                    ("Implement planner product UI", "Implement product planner UI flow."),
                    ("Implement supermarket API integration", "Implement API integration runtime backend."),
                    ("Implement regression quality tests", "Implement quality validation regression tests."),
                ],
            },
            "raiseai": {
                "priority": "P2",
                "cap": 1,
                "tasks": [
                    ("Implement device microphone integration", "Implement watch device microphone integration."),
                ],
            },
            "haxlab": {
                "priority": "P2",
                "cap": 1,
                "tasks": [
                    ("Implement model training rollout", "Implement model training rollout policy."),
                ],
            },
        }
        with server.connect() as conn:
            for project_id, spec in self.projects.items():
                cap = spec["cap"]
                conn.execute(
                    "UPDATE runner_targets SET worker_count=? WHERE project_id=?",
                    (cap, project_id),
                )
                for slot in range(1, cap + 1):
                    conn.execute(
                        "INSERT INTO runner_workers(project_id,worker_slot,conversation_id,desired_state,provider) "
                        "VALUES(?,?,?,?,?)",
                        (
                            project_id,
                            slot,
                            f"load-{project_id}-{slot:02d}",
                            "running",
                            "chatgpt",
                        ),
                    )

        for project_id, spec in self.projects.items():
            for ordinal, (title, criteria) in enumerate(spec["tasks"], 1):
                server.portfolio_queue_enqueue(
                    project_id,
                    title,
                    spec["priority"],
                    criteria,
                    queue_id=f"load-{project_id}-{ordinal:02d}",
                )

        selected = server.portfolio_queue_allocate()
        server._persist_global_worker_allocation(server.global_worker_allocation())
        self.assertEqual(8, len(selected))
        self.initial_runner_worker_rows = self._runner_worker_row_count()

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(server, key, value)
        self.tmp.cleanup()

    def _runner_worker_row_count(self):
        with server.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM runner_workers").fetchone()[0]

    def _check_snapshot(self, errors, metrics, lock):
        snapshot = server.portfolio_queue_allocation()
        base = server.runner_targets()
        targets = server.runner_worker_targets(
            allocation=snapshot,
            base=base,
            reconcile=False,
        )
        workers = list(snapshot.get("workers") or [])
        slots = [int(item["global_worker_slot"]) for item in workers]
        keys = [str(item["worker_key"]) for item in workers]
        counts = collections.Counter(str(item["project_id"]) for item in workers)

        local_errors = []
        if len(workers) > 8:
            local_errors.append(f"worker-limit:{len(workers)}")
        if len(slots) != len(set(slots)):
            local_errors.append(f"duplicate-global-slot:{slots}")
        if len(keys) != len(set(keys)):
            local_errors.append(f"duplicate-worker-key:{keys}")
        if any(slot < 1 or slot > 8 for slot in slots):
            local_errors.append(f"slot-out-of-range:{slots}")

        for project_id, count in counts.items():
            cap = int(server.project_runtime.project_contract(project_id)["ai_worker_cap"])
            if count > cap:
                local_errors.append(f"project-cap:{project_id}:{count}>{cap}")

        for item in workers:
            key = item["worker_key"]
            target = targets.get(key)
            if target is None:
                local_errors.append(f"missing-target:{key}")
                continue
            if target.get("base_project_id") != item["project_id"]:
                local_errors.append(
                    f"project-cross-wire:{key}:{target.get('base_project_id')}!={item['project_id']}"
                )
            if target.get("global_worker_slot") != item["global_worker_slot"]:
                local_errors.append(
                    f"slot-cross-wire:{key}:{target.get('global_worker_slot')}!={item['global_worker_slot']}"
                )
            queue_item = target.get("queue_item")
            if queue_item is not None:
                if queue_item.get("queue_id") != item["queue_id"]:
                    local_errors.append(
                        f"queue-cross-wire:{key}:{queue_item.get('queue_id')}!={item['queue_id']}"
                    )
                if queue_item.get("project_id") != item["project_id"]:
                    local_errors.append(
                        f"queue-project-cross-wire:{key}:{queue_item.get('project_id')}"
                    )
            elif target.get("assignment_ready"):
                local_errors.append(f"ready-without-queue:{key}")

        with lock:
            errors.extend(local_errors)
            metrics["max_workers"] = max(metrics["max_workers"], len(workers))
            metrics["samples"] += 1
            metrics["projects"].update(counts)

    def test_eight_slot_multi_project_churn_keeps_read_model_coherent(self):
        barrier = threading.Barrier(9)
        lock = threading.Lock()
        errors = []
        metrics = {"max_workers": 0, "samples": 0, "projects": set()}

        def reader():
            barrier.wait()
            for _ in range(15):
                self._check_snapshot(errors, metrics, lock)

        def writer():
            barrier.wait()
            for iteration in range(30):
                snapshot = server.portfolio_queue_allocation()
                workers = list(snapshot.get("workers") or [])
                if workers:
                    item = workers[iteration % len(workers)]
                    result = server.portfolio_queue_finish(
                        item["global_worker_slot"],
                        item["queue_id"],
                        "CONTINUE",
                        evidence="synthetic multi-project load churn",
                    )
                    if not result.get("updated"):
                        with lock:
                            errors.append(
                                f"writer-assignment-mismatch:{item['queue_id']}:{item['global_worker_slot']}"
                            )
                server.portfolio_queue_allocate()
                server._persist_global_worker_allocation(server.global_worker_allocation())

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=9) as pool:
            futures = [pool.submit(reader) for _ in range(8)]
            futures.append(pool.submit(writer))
            for future in futures:
                future.result(timeout=30)
        elapsed = time.monotonic() - started

        self._check_snapshot(errors, metrics, lock)

        settle_counts = []
        for _ in range(4):
            selected = server.portfolio_queue_allocate()
            server._persist_global_worker_allocation(server.global_worker_allocation())
            settle_counts.append(len(selected))
            if len(selected) == 8:
                break
        final = server.portfolio_queue_allocation()

        self.assertEqual([], errors)
        self.assertEqual(8, metrics["max_workers"])
        self.assertGreaterEqual(metrics["samples"], 121)
        self.assertGreaterEqual(len(metrics["projects"]), 6)
        self.assertEqual(
            8,
            len(final["workers"]),
            f"allocator did not reconverge to 8 slots after bounded settle ticks: {settle_counts}",
        )
        self.assertEqual(
            self.initial_runner_worker_rows,
            self._runner_worker_row_count(),
            "read-model load must not reconcile/write runner_workers",
        )
        self.assertLess(
            elapsed,
            25.0,
            f"bounded synthetic load exceeded 25s budget: {elapsed:.3f}s",
        )


if __name__ == "__main__":
    unittest.main()
