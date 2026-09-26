import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import sys
import types
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
sys.modules["enhancements"] = enhancements_stub

import server


class TaskClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-claims-")
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

    def test_claim_is_durable_across_init_db(self):
        result = server.task_claim_acquire("cloud", "notion:task-1", "owner-a", "cloud::w1", 120)
        self.assertTrue(result["acquired"])
        server.init_db()
        claims = server.task_claims("cloud")
        self.assertEqual(1, len(claims))
        self.assertEqual("owner-a", claims[0]["owner_id"])

    def test_two_workers_cannot_claim_same_task(self):
        barrier = threading.Barrier(3)
        results = []
        lock = threading.Lock()

        def worker(owner):
            barrier.wait()
            result = server.task_claim_acquire("cloud", "notion:race", owner, owner, 120)
            with lock:
                results.append((owner, result["acquired"]))

        threads = [threading.Thread(target=worker, args=(owner,)) for owner in ("owner-a", "owner-b")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        winners = [owner for owner, acquired in results if acquired]
        self.assertEqual(1, len(winners), results)
        claims = server.task_claims("cloud")
        race = next(c for c in claims if c["claim_key"] == "notion:race")
        self.assertEqual(winners[0], race["owner_id"])

    def test_stale_claim_is_automatically_recovered(self):
        expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with server.connect() as conn:
            conn.execute(
                """INSERT INTO task_claims(project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("cloud", "notion:stale", "dead-owner", "cloud::w1", old, old, expired, "{}"),
            )
        result = server.task_claim_acquire("cloud", "notion:stale", "new-owner", "cloud::w2", 120)
        self.assertTrue(result["acquired"])
        self.assertEqual("new-owner", result["claim"]["owner_id"])

    def test_heartbeat_requires_current_unexpired_owner(self):
        first = server.task_claim_acquire("cloud", "notion:heartbeat", "owner-a", "cloud::w1", 60)
        before = first["claim"]["lease_until"]
        wrong = server.task_claim_heartbeat("cloud", "notion:heartbeat", "owner-b", 600)
        self.assertFalse(wrong["renewed"])
        renewed = server.task_claim_heartbeat("cloud", "notion:heartbeat", "owner-a", 600)
        self.assertTrue(renewed["renewed"])
        self.assertGreater(renewed["claim"]["lease_until"], before)

    def test_claim_api_conflict_release_and_reacquire(self):
        status, first = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-a", "worker_id": "cloud::w1", "lease_seconds": 120},
        )
        self.assertEqual(200, status, first)
        status, conflict = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-b", "worker_id": "cloud::w2", "lease_seconds": 120},
        )
        self.assertEqual(409, status, conflict)
        self.assertEqual("owner-a", conflict["claim"]["owner_id"])

        status, released = self.request(
            "/api/task-claims",
            {"action": "release", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-a"},
        )
        self.assertEqual(200, status, released)
        status, second = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-b", "worker_id": "cloud::w2", "lease_seconds": 120},
        )
        self.assertEqual(200, status, second)
        self.assertTrue(second["acquired"])


if __name__ == "__main__":
    unittest.main(verbosity=2)