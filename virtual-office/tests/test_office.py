"""Isolated standard-library tests. No Codex login, tokens, VPS or network required."""
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(server, "DB", Path(self.tmp.name) / "office.db")
        self.root_patch = patch.object(server, "WORKSPACE", Path(self.tmp.name) / "workspace")
        self.write_patch = patch.object(server, "ALLOW_WRITES", False)
        self.db_patch.start(); self.root_patch.start(); self.write_patch.start()
        self.addCleanup(self.db_patch.stop); self.addCleanup(self.root_patch.stop); self.addCleanup(self.write_patch.stop)
        server.init_db()

    def test_persistent_queue_and_memory(self):
        job = server.enqueue("maya", "chat", "Wat is het plan?")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(server.get_job(job["id"])["instruction"], "Wat is het plan?")
        chosen = server.claim()
        self.assertEqual(chosen["id"], job["id"])
        self.assertIsNone(server.claim())
        server.finish(job["id"], "done", output="We beginnen met UX.")
        self.assertEqual(server.get_job(job["id"])["status"], "done")
        with server.connect() as db:
            messages = db.execute("SELECT role,content FROM messages ORDER BY id").fetchall()
        self.assertEqual([row["role"] for row in messages], ["user", "assistant"])
        self.assertIn("UX", server.prompt_for(job))
        # Reopening SQLite preserves completed work after the browser closes.
        server.init_db()
        self.assertEqual(server.get_job(job["id"])["status"], "done")

    def test_crash_does_not_replay_write(self):
        with patch.object(server, "ALLOW_WRITES", True):
            job = server.enqueue("noah", "task", "Schrijf een test")
        server.claim()
        server.init_db()
        self.assertEqual(server.get_job(job["id"])["status"], "interrupted")
        self.assertIsNone(server.claim())
        self.assertEqual(server.retry(job["id"])["status"], "queued")

    def test_write_disabled_by_default(self):
        with self.assertRaisesRegex(ValueError, "Schrijftaken staan uit"):
            server.enqueue("noah", "task", "Verwijder de code")
        self.assertEqual(server.list_jobs(), [])

    def test_invalid_input_and_persona(self):
        with self.assertRaises(ValueError):
            server.enqueue("nobody", "chat", "hoi")
        with self.assertRaises(ValueError):
            server.enqueue("maya", "chat", " " * 40)
        with self.assertRaises(ValueError):
            server.enqueue("maya", "chat", "x" * 4000)
        self.assertIn("Projectmanager", server.prompt_for(server.enqueue("maya", "chat", "Hoi")))

    def test_atomic_claim(self):
        server.enqueue("lina", "chat", "Doe een QA-inspectie")
        barrier = threading.Barrier(3)
        def attempt(_):
            barrier.wait()
            return server.claim()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(attempt, range(3)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_failed_retry_and_no_duplicate_finish(self):
        job = server.enqueue("lina", "chat", "Controleer contrast")
        server.claim()
        server.finish(job["id"], "failed", error="geen login")
        self.assertEqual(server.retry(job["id"])["status"], "queued")
        server.finish(job["id"], "done", output="mag niet")
        self.assertEqual(server.get_job(job["id"])["status"], "queued")


class HTTPTests(OfficeTests):
    def setUp(self):
        super().setUp()
        self.auth_patch = patch.object(server, "TOKEN", "test-only-password")
        self.codex_patch = patch.object(server, "available_codex", return_value=False)
        self.auth_patch.start(); self.codex_patch.start()
        self.addCleanup(self.auth_patch.stop); self.addCleanup(self.codex_patch.stop)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(self.http.server_close)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.http.shutdown(), self.thread.join(timeout=2)))

    def ask(self, path, body=None, token=None, content_type="application/json"):
        headers={}
        if token: headers["X-Office-Token"]=token
        if body is not None: headers["Content-Type"]=content_type
        req=Request("http://127.0.0.1:"+str(self.http.server_address[1])+path,
                    data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, json.load(e)

    def test_security_and_health(self):
        code,health=self.ask("/api/health")
        self.assertEqual(code,200)
        self.assertFalse(health["codex_ready"])
        self.assertTrue(health["auth_required"])
        self.assertEqual(self.ask("/api/jobs")[0],401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","instruction":"Hey"})[0],401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","instruction":"Hey"},token="wrong")[0],401)
        self.assertEqual(self.ask("/api/jobs",{"employee":"maya","instruction":"Hey"},token="test-only-password",content_type="text/plain")[0],415)
        code,job=self.ask("/api/jobs",{"employee":"maya","instruction":"Hey"},token="test-only-password")
        self.assertEqual(code,201)
        self.assertEqual(self.ask("/api/jobs",token="test-only-password")[1]["jobs"][0]["id"],job["id"])

if __name__ == "__main__":
    unittest.main()
