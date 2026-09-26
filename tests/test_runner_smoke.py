import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import sys
import types
from http.server import ThreadingHTTPServer
from pathlib import Path

# Runner-control smoke tests deliberately isolate unrelated VPS telemetry/resource helpers.
# The production enhancements module has host-specific import side effects, so use the
# smallest stub needed by server.init_db() in this portable test process.
enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
sys.modules["enhancements"] = enhancements_stub

import server


class RunnerSmokeTests(unittest.TestCase):
    """Critical runner flows against an isolated temporary zCloud state DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-smoke-")
        self.original_db = server.DB
        self.original_cache = server.CACHE
        server.DB = Path(self.tmp.name) / "history.db"
        server.CACHE = None
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
        server.DB = self.original_db
        server.CACHE = self.original_cache
        self.tmp.cleanup()

    def request(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def active(self, project_id="cloud"):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT active FROM runner_targets WHERE project_id=?", (project_id,)
            ).fetchone()
        return bool(row["active"])

    def test_cold_start_creates_runner_state(self):
        with server.connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            cloud = conn.execute(
                "SELECT project_id, active, worker_count FROM runner_targets WHERE project_id='cloud'"
            ).fetchone()
        self.assertIn("runner_targets", tables)
        self.assertIn("runner_workers", tables)
        self.assertIsNotNone(cloud)
        self.assertFalse(bool(cloud["active"]))
        self.assertGreaterEqual(int(cloud["worker_count"]), 1)

    def test_start_push_pause_flow(self):
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        self.assertTrue(self.active())

        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(200, status, body)
        self.assertEqual("pending", body["status"])

        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "pause"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        self.assertFalse(self.active())

    def test_push_requires_started_project(self):
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(409, status)
        self.assertIn("Start dit project eerst", body["error"])

    def test_repeated_start_is_rate_limited(self):
        first_status, _ = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        second_status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, first_status)
        self.assertEqual(429, second_status)
        self.assertIn("net al een actie", body["error"])

    def test_worker_count_change_updates_worker_targets(self):
        status, body = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": 3}
        )
        self.assertEqual(200, status, body)
        self.assertEqual(3, body["worker_count"])

        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        cloud_workers = sorted(
            key for key in targets["projects"] if key.startswith("cloud::w")
        )
        self.assertEqual(["cloud::w1", "cloud::w2", "cloud::w3"], cloud_workers)
        self.assertTrue(all(targets["projects"][key]["worker_count"] == 3 for key in cloud_workers))

    def test_worker_count_rejects_unsafe_value(self):
        status, body = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": server.MAX_CHATGPT_WORKERS + 1}
        )
        self.assertEqual(400, status)
        self.assertIn("ChatGPT-tabs", body["error"])

    def test_reconnect_adopts_and_persists_conversation(self):
        status, _ = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": 2}
        )
        self.assertEqual(200, status)
        conversation_id = "12345678-1234-1234-1234-123456789abc"
        status, body = self.request(
            "/api/runner-status",
            {
                "event": "conversation-adopted",
                "projectId": "cloud::w2",
                "baseProjectId": "cloud",
                "workerSlot": 2,
                "target": f"https://chatgpt.com/c/{conversation_id}",
                "title": "zCloud · worker 2/2",
            },
        )
        self.assertEqual(200, status, body)

        # Model a zCloud/service restart: schema/bootstrap must preserve the adopted mapping.
        server.init_db()
        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        self.assertEqual(conversation_id, targets["projects"]["cloud::w2"]["conversation_id"])
        self.assertEqual(
            f"https://chatgpt.com/c/{conversation_id}",
            targets["projects"]["cloud::w2"]["url"],
        )

    def test_worker_pause_is_individual_and_persistent(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "pause"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(self.active("cloud"))
        with server.connect() as conn:
            row = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()
        self.assertEqual("paused", row["desired_state"])
        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        self.assertTrue(targets["projects"]["cloud::w1"]["active"])
        self.assertFalse(targets["projects"]["cloud::w2"]["active"])
        server.init_db()
        with server.connect() as conn:
            row = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()
        self.assertEqual("paused", row["desired_state"])

    def test_worker_drain_finishes_into_paused_state(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "drain"}
        )
        self.assertEqual(200, status, body)
        self.assertEqual("draining", body["desired_state"])
        status, targets = self.request("/api/runner-targets")
        self.assertTrue(targets["projects"]["cloud::w2"]["active"])
        self.assertEqual("draining", targets["projects"]["cloud::w2"]["desired_state"])
        status, body = self.request(
            "/api/runner-status",
            {"event": "runner-drained", "projectId": "cloud::w2",
             "baseProjectId": "cloud", "workerSlot": 2, "title": "zCloud · worker 2/2"},
        )
        self.assertEqual(200, status, body)
        with server.connect() as conn:
            desired = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()["desired_state"]
        self.assertEqual("paused", desired)

    def test_project_push_rejects_when_all_workers_are_paused(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        first, _ = self.request(
            "/api/runner-control", {"project_id": "cloud::w1", "action": "pause"}
        )
        second, _ = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "pause"}
        )
        self.assertEqual((200, 200), (first, second))
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(409, status)
        self.assertIn("Geen actieve workers", body["error"])

    def test_worker_status_exposes_task_claim_and_advanced_metadata(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        server.task_claim_acquire(
            "cloud", "notion:abc", "owner-a", "cloud::w1", 300,
            {"task": "Veilige workerkaart bouwen", "branch": "worker/test"},
        )
        self.request(
            "/api/runner-status",
            {"event": "heartbeat", "projectId": "cloud::w1",
             "baseProjectId": "cloud", "workerSlot": 1, "title": "zCloud · worker 1/1"},
        )
        cloud = server.runner_statuses()["cloud"]
        self.assertEqual(1, cloud["desired_worker_count"])
        self.assertEqual(1, cloud["active_worker_count"])
        worker = cloud["workers"][0]
        self.assertEqual("cloud::w1", worker["worker_id"])
        self.assertTrue(worker["work_area"])
        self.assertEqual("Veilige workerkaart bouwen", worker["current_task"]["title"])
        self.assertEqual("worker/test", worker["current_task"]["branch"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
