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


if __name__ == "__main__":
    unittest.main(verbosity=2)
