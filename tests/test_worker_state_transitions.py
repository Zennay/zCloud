import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class DeterministicWorkerStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-state-machine-")
        self.saved = {
            "DB": server.DB,
            "LAYOUT_FILE": server.LAYOUT_FILE,
            "CACHE": server.CACHE,
            "GLOBAL_CHATGPT_WORKER_LIMIT": server.GLOBAL_CHATGPT_WORKER_LIMIT,
            "MAX_CHATGPT_WORKERS": server.MAX_CHATGPT_WORKERS,
            "worker_memory_status": server.worker_memory_status,
        }
        root = Path(self.tmp.name)
        server.DB = root / "history.db"
        server.LAYOUT_FILE = root / "project-layout.json"
        server.CACHE = None
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 2
        server.MAX_CHATGPT_WORKERS = 2
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

    def request(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def clear_commands(self):
        with server.connect() as conn:
            conn.execute("DELETE FROM runner_commands")

    def target_active(self, project_id="cloud"):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT active FROM runner_targets WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return bool(row["active"])

    def worker_states(self, project_id="cloud"):
        with server.connect() as conn:
            rows = conn.execute(
                """SELECT worker_slot,desired_state
                   FROM runner_workers
                   WHERE project_id=?
                   ORDER BY worker_slot""",
                (project_id,),
            ).fetchall()
        return {int(row["worker_slot"]): str(row["desired_state"]) for row in rows}

    def test_project_and_worker_state_machine_is_deterministic(self):
        status, body = self.request(
            "/api/runner-workers",
            {"project_id": "cloud", "worker_count": 2},
        )
        self.assertEqual(200, status, body)
        self.assertEqual({1: "running", 2: "running"}, self.worker_states())

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.assertTrue(self.target_active())
        self.assertEqual({1: "running", 2: "running"}, self.worker_states())
        self.clear_commands()

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud::w2", "action": "drain"},
        )
        self.assertEqual(200, status, body)
        self.assertEqual("draining", body["desired_state"])
        self.assertEqual({1: "running", 2: "draining"}, self.worker_states())
        self.clear_commands()

        server.runner_record(
            {
                "event": "runner-drained",
                "projectId": "cloud::w2",
                "baseProjectId": "cloud",
                "workerSlot": 2,
                "globalWorkerSlot": 2,
                "reason": "dashboard-drain",
                "provider": "chatgpt",
            }
        )
        self.assertEqual({1: "running", 2: "paused"}, self.worker_states())

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud::w2", "action": "push"},
        )
        self.assertEqual(409, status, body)
        self.assertEqual({1: "running", 2: "paused"}, self.worker_states())

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud::w2", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.assertEqual("running", body["desired_state"])
        self.assertEqual({1: "running", 2: "running"}, self.worker_states())
        self.clear_commands()

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud::w2", "action": "pause"},
        )
        self.assertEqual(200, status, body)
        self.assertEqual("paused", body["desired_state"])
        self.assertEqual({1: "running", 2: "paused"}, self.worker_states())
        self.clear_commands()

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "pause"},
        )
        self.assertEqual(200, status, body)
        self.assertFalse(self.target_active())
        self.assertEqual({1: "paused", 2: "paused"}, self.worker_states())
        self.clear_commands()

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.assertTrue(self.target_active())
        self.assertEqual({1: "running", 2: "running"}, self.worker_states())

    def test_project_start_converges_mixed_worker_states_to_running(self):
        status, body = self.request(
            "/api/runner-workers",
            {"project_id": "cloud", "worker_count": 2},
        )
        self.assertEqual(200, status, body)
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET active=0 WHERE project_id='cloud'")
            conn.execute(
                "UPDATE runner_workers SET desired_state='paused' "
                "WHERE project_id='cloud' AND worker_slot=1"
            )
            conn.execute(
                "UPDATE runner_workers SET desired_state='draining' "
                "WHERE project_id='cloud' AND worker_slot=2"
            )

        self.assertEqual({1: "paused", 2: "draining"}, self.worker_states())
        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.assertTrue(self.target_active())
        self.assertEqual({1: "running", 2: "running"}, self.worker_states())


if __name__ == "__main__":
    unittest.main()
