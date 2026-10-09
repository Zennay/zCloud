"""Real HTTP callback regression against an isolated temporary database.

These cases intentionally track known production defects as expected failures.
They must become normal assertions when the serialized server owner fixes them.
"""
import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# Match test_runner_smoke's portable host-specific import isolation.
if "enhancements" not in sys.modules:
    enhancements_stub = types.ModuleType("enhancements")
    enhancements_stub.init_db = lambda conn: None
    enhancements_stub.load_resource_policy = lambda: {}
    sys.modules["enhancements"] = enhancements_stub

import server


class RunnerCommandReceiptHttpW4(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-receipt-http-")
        self.old_db = server.DB
        self.old_cache = server.CACHE
        server.DB = Path(self.tmp.name) / "history.db"
        server.CACHE = None
        server.init_db()
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands (project_id,action,status,created_at,updated_at,result)"
                " VALUES ('cloud','push','pending','2026-10-09','2026-10-09',NULL)"
            )
            self.command_id = conn.execute("SELECT max(id) FROM runner_commands").fetchone()[0]
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        server.DB = self.old_db
        server.CACHE = self.old_cache
        self.tmp.cleanup()

    def send(self, command_id, status="completed", result="first"):
        payload = json.dumps({"command_id": command_id, "status": status, "result": result}).encode()
        url = "http://127.0.0.1:%d/api/runner-command-result" % self.http.server_port
        request = urllib.request.Request(url, data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    def state(self):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT status,result,updated_at FROM runner_commands WHERE id=?",
                (self.command_id,),
            ).fetchone()
            return tuple(row)

    def test_valid_receipt_updates_only_its_command(self):
        self.assertEqual(200, self.send(self.command_id, result="first accepted"))
        self.assertEqual(("completed", "first accepted"), self.state()[:2])

    def test_invalid_status_is_rejected_without_mutation(self):
        before = self.state()
        self.assertEqual(400, self.send(self.command_id, status="pending"))
        self.assertEqual(before, self.state())

    def test_unknown_positive_id_changes_no_rows(self):
        before = self.state()
        self.send(self.command_id + 50000, result="ghost")
        self.assertEqual(before, self.state())

    def test_pending_command_id_is_serialized_as_json_integer(self):
        url = "http://127.0.0.1:%d/api/runner-commands" % self.http.server_port
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(200, response.status)
            payload = json.load(response)
        matches = [command for command in payload["commands"]
                   if command["id"] == self.command_id]
        self.assertEqual(1, len(matches))
        self.assertIs(type(matches[0]["id"]), int)
        self.assertGreater(matches[0]["id"], 0)

    @unittest.expectedFailure
    def test_competing_http_callbacks_have_one_terminal_winner(self):
        # Two real HTTP requests race on the same pending row. Only one may
        # commit; a late callback must not alter even the stored result text.
        barrier = threading.Barrier(3)
        outcomes = []
        failures = []
        lock = threading.Lock()

        def complete(status, result):
            try:
                barrier.wait(timeout=5)
                response_status = self.send(self.command_id, status=status, result=result)
                with lock:
                    outcomes.append((response_status, status, result))
            except Exception as error:
                with lock:
                    failures.append(error)

        workers = [
            threading.Thread(target=complete, args=("completed", "winner-success")),
            threading.Thread(target=complete, args=("failed", "winner-failure")),
        ]
        for worker in workers:
            worker.start()
        try:
            barrier.wait(timeout=5)
        finally:
            for worker in workers:
                worker.join(timeout=7)
        self.assertFalse(failures, failures)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(2, len(outcomes))
        self.assertEqual([200, 409], sorted(item[0] for item in outcomes))
        winning = next(item for item in outcomes if item[0] == 200)
        self.assertEqual((winning[1], winning[2]), self.state()[:2])

    @unittest.expectedFailure
    def test_replayed_callback_must_not_overwrite_terminal_result(self):
        self.assertEqual(200, self.send(self.command_id, result="winner"))
        before = self.state()
        self.send(self.command_id, status="failed", result="late failure")
        self.assertEqual(before, self.state(), "terminal result overwritten by replay")

    @unittest.expectedFailure
    def test_boolean_id_must_be_rejected_without_mutation(self):
        before = self.state()
        self.assertEqual(400, self.send(True))
        self.assertEqual(before, self.state())

    @unittest.expectedFailure
    def test_zero_id_must_be_rejected_without_mutation(self):
        before = self.state()
        self.assertEqual(400, self.send(0))
        self.assertEqual(before, self.state())


if __name__ == "__main__":
    unittest.main()
