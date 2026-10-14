import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class MultiWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-multiworker-lifecycle-")
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

    def request(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
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

    def assert_mapping(self, conv1, conv2, *, active1, active2):
        status, body = self.request("/api/runner-targets")
        self.assertEqual(200, status, body)
        workers = body["projects"]
        self.assertEqual(conv1, workers["cloud::w1"]["conversation_id"])
        self.assertEqual(conv2, workers["cloud::w2"]["conversation_id"])
        self.assertEqual(active1, workers["cloud::w1"]["active"])
        self.assertEqual(active2, workers["cloud::w2"]["active"])

    def test_two_worker_start_pause_resume_reconnect_keeps_mapping(self):
        conv1 = "11111111-aaaa-4111-8111-111111111111"
        conv2 = "22222222-bbbb-4222-8222-222222222222"

        status, body = self.request(
            "/api/runner-workers",
            {"project_id": "cloud", "worker_count": 2},
        )
        self.assertEqual(200, status, body)

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.clear_commands()

        for slot, conversation_id in ((1, conv1), (2, conv2)):
            status, body = self.request(
                "/api/runner-status",
                {
                    "event": "conversation-adopted",
                    "projectId": f"cloud::w{slot}",
                    "baseProjectId": "cloud",
                    "workerSlot": slot,
                    "globalWorkerSlot": slot,
                    "provider": "chatgpt",
                    "target": f"https://chatgpt.com/c/{conversation_id}",
                    "title": f"zCloud · worker {slot}/2",
                },
            )
            self.assertEqual(200, status, body)

        self.assert_mapping(conv1, conv2, active1=True, active2=True)

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud::w2", "action": "pause"},
        )
        self.assertEqual(200, status, body)
        self.clear_commands()
        self.assert_mapping(conv1, conv2, active1=True, active2=False)

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "pause"},
        )
        self.assertEqual(200, status, body)
        self.clear_commands()
        self.assert_mapping(conv1, conv2, active1=False, active2=False)

        status, body = self.request(
            "/api/runner-control",
            {"project_id": "cloud", "action": "start"},
        )
        self.assertEqual(200, status, body)
        self.clear_commands()
        self.assert_mapping(conv1, conv2, active1=True, active2=True)

        status, body = self.request(
            "/api/runner-status",
            {
                "event": "conversation-adopted",
                "projectId": "cloud::w2",
                "baseProjectId": "cloud",
                "workerSlot": 2,
                "globalWorkerSlot": 2,
                "provider": "chatgpt",
                "target": f"https://chatgpt.com/c/{conv2}",
                "title": "zCloud · worker 2/2 reconnect",
            },
        )
        self.assertEqual(200, status, body)

        server.init_db()
        self.assert_mapping(conv1, conv2, active1=True, active2=True)

        with server.connect() as conn:
            rows = conn.execute(
                """SELECT worker_slot,conversation_id,desired_state
                   FROM runner_workers
                   WHERE project_id='cloud'
                   ORDER BY worker_slot"""
            ).fetchall()
        self.assertEqual(
            [
                (1, conv1, "running"),
                (2, conv2, "running"),
            ],
            [
                (int(row["worker_slot"]), row["conversation_id"], row["desired_state"])
                for row in rows
            ],
        )


if __name__ == "__main__":
    unittest.main()
