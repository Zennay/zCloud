import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class WorkerCountRaceTests(unittest.TestCase):
    ROUNDS = 20

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-worker-count-race-")
        self.saved = {
            "DB": server.DB,
            "PORTFOLIO_QUEUE_SEED_FILE": server.PORTFOLIO_QUEUE_SEED_FILE,
            "GLOBAL_CHATGPT_WORKER_LIMIT": server.GLOBAL_CHATGPT_WORKER_LIMIT,
            "MAX_CHATGPT_WORKERS": server.MAX_CHATGPT_WORKERS,
            "worker_memory_status": server.worker_memory_status,
        }
        root = Path(self.tmp.name)
        server.DB = root / "history.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]\n", encoding="utf-8")
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.MAX_CHATGPT_WORKERS = 3
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
        server.init_db()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        for key, value in self.saved.items():
            setattr(server, key, value)
        self.tmp.cleanup()

    def request_worker_count(self, count: int, actor: str):
        payload = json.dumps({
            "project_id": "cloud",
            "worker_count": count,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/runner-workers",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-ZCloud-Actor": actor,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def reset_round(self, round_id: int):
        actor_prefix = f"worker-count-race-{round_id}-"
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'"
            )
            conn.execute(
                "DELETE FROM runner_workers "
                "WHERE project_id='cloud' AND worker_slot>1"
            )
            conn.execute(
                "DELETE FROM config_audit WHERE actor LIKE ?",
                (actor_prefix + "%",),
            )

    def audit_rows(self, round_id: int):
        actor_prefix = f"worker-count-race-{round_id}-"
        with server.connect() as conn:
            rows = conn.execute(
                "SELECT id,actor,old_value_json,new_value_json,result "
                "FROM config_audit "
                "WHERE actor LIKE ? AND config_key='runner.worker_count' "
                "AND target='cloud' ORDER BY id",
                (actor_prefix + "%",),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "actor": row["actor"],
                "old": json.loads(row["old_value_json"]),
                "new": json.loads(row["new_value_json"]),
                "result": row["result"],
            }
            for row in rows
        ]

    def state_snapshot(self):
        with server.connect() as conn:
            target = conn.execute(
                "SELECT worker_count FROM runner_targets WHERE project_id='cloud'"
            ).fetchone()
            slots = [
                int(row["worker_slot"])
                for row in conn.execute(
                    "SELECT worker_slot FROM runner_workers "
                    "WHERE project_id='cloud' ORDER BY worker_slot"
                )
            ]
        return int(target["worker_count"]), slots

    def test_concurrent_worker_count_updates_are_serialized_and_restart_safe(self):
        for round_id in range(self.ROUNDS):
            with self.subTest(round=round_id):
                self.reset_round(round_id)
                barrier = threading.Barrier(3)
                lock = threading.Lock()
                results = []
                errors = []

                def write(count: int):
                    try:
                        barrier.wait(timeout=3)
                        result = self.request_worker_count(
                            count,
                            f"worker-count-race-{round_id}-{count}",
                        )
                        with lock:
                            results.append((count, result))
                    except Exception as exc:
                        with lock:
                            errors.append(repr(exc))

                threads = [
                    threading.Thread(target=write, args=(2,)),
                    threading.Thread(target=write, args=(3,)),
                ]
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=3)
                for thread in threads:
                    thread.join(timeout=7)

                self.assertEqual([], errors)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual(2, len(results), results)
                self.assertEqual(
                    [200, 200],
                    sorted(status for _, (status, _) in results),
                    results,
                )

                final_count, slots = self.state_snapshot()
                self.assertIn(final_count, {2, 3})
                self.assertEqual(
                    list(range(1, final_count + 1)),
                    slots[:final_count],
                    (final_count, slots),
                )
                self.assertEqual(len(slots), len(set(slots)))

                audit = self.audit_rows(round_id)
                self.assertEqual(2, len(audit), audit)
                self.assertEqual({2, 3}, {row["new"] for row in audit})
                self.assertEqual(1, audit[0]["old"], audit)
                self.assertEqual(audit[0]["new"], audit[1]["old"], audit)
                self.assertEqual(final_count, audit[1]["new"], audit)
                self.assertTrue(
                    all(row["result"] == "succeeded" for row in audit),
                    audit,
                )

        before_restart = self.state_snapshot()
        server.init_db()
        after_restart = self.state_snapshot()
        self.assertEqual(before_restart, after_restart)


if __name__ == "__main__":
    unittest.main()
