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
        # LIFO ordering: this assertion runs after shutdown, global restore
        # and temporary-file deletion, even if setUp() raises partway through.
        self.addCleanup(self.assert_fixture_fully_cleaned)
        self.addCleanup(self.tmp.cleanup)
        self.old_db = server.DB
        self.old_cache = server.CACHE
        self.old_layout_file = server.LAYOUT_FILE
        # init_db() loads dynamic worker policy globals from the test DB.
        # Restore them to avoid contaminating unrelated tests discovered later.
        self.saved_policy = {
            key: getattr(server, key)
            for key in (
                "GLOBAL_CHATGPT_WORKER_LIMIT", "DYNAMIC_CHATGPT_WORKERS",
                "DYNAMIC_CLAUDE_WORKERS", "DYNAMIC_CHATGPT_COOLDOWN_SECONDS",
                "DYNAMIC_CLAUDE_COOLDOWN_SECONDS",
                "DYNAMIC_WORKER_CHECK_INTERVAL_MS",
                "DYNAMIC_WORKER_TICK_INTERVAL_MS",
                "DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS",
                "DYNAMIC_WORKER_GENERATION_TIMEOUT_MS",
                "AUTONOMY_TICK_SECONDS",
            )
        }
        # Register restoration before any initialization that can raise, so a
        # failed setUp cannot leak DB paths/policy into subsequent test classes.
        self.addCleanup(self.restore_server_globals)
        server.DB = Path(self.tmp.name) / "history.db"
        server.LAYOUT_FILE = Path(self.tmp.name) / "project-layout.json"
        server.CACHE = None
        server.init_db()
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands (project_id,action,status,created_at,updated_at,result)"
                " VALUES ('cloud','push','pending','2026-10-09','2026-10-09',NULL)"
            )
            self.command_id = conn.execute("SELECT max(id) FROM runner_commands").fetchone()[0]
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(self.http.server_close)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        # Only register shutdown once the server thread actually started:
        # shutdown() before serve_forever() starts could deadlock.
        self.thread.start()
        self.addCleanup(self.stop_http_thread)

    def assert_fixture_fully_cleaned(self):
        self.assertFalse(Path(self.tmp.name).exists(), "temporary fixture leaked")
        # On a partial setUp failure, only compare snapshots actually taken.
        # Never replace the original setup exception with AttributeError.
        if hasattr(self, "old_db"):
            self.assertEqual(self.old_db, server.DB)
        if hasattr(self, "old_layout_file"):
            self.assertEqual(self.old_layout_file, server.LAYOUT_FILE)
        if hasattr(self, "old_cache"):
            self.assertIs(self.old_cache, server.CACHE)
        if hasattr(self, "saved_policy"):
            for key, value in self.saved_policy.items():
                self.assertEqual(value, getattr(server, key), key)
        if hasattr(self, "http"):
            self.assertEqual(-1, self.http.socket.fileno(), "HTTP listener leaked")
        if hasattr(self, "thread"):
            self.assertFalse(self.thread.is_alive(), "HTTP thread leaked")

    def stop_http_thread(self):
        self.http.shutdown()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive(), "temporary HTTP server thread leaked")

    def restore_server_globals(self):
        server.DB = self.old_db
        server.LAYOUT_FILE = self.old_layout_file
        server.CACHE = self.old_cache
        for key, value in self.saved_policy.items():
            setattr(server, key, value)

    def send(self, command_id, status="completed", result="first"):
        payload = json.dumps({"command_id": command_id, "status": status, "result": result}).encode()
        url = "http://127.0.0.1:%d/api/runner-command-result" % self.http.server_port
        request = urllib.request.Request(url, data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status_code = response.status
                body = json.load(response)
        except urllib.error.HTTPError as error:
            status_code = error.code
            try:
                body = json.load(error)
            finally:
                error.close()
        # A transport-level 200 with malformed or false success JSON is not
        # a valid acknowledgement; reject it even in normal-path assertions.
        self.assertIsInstance(body, dict, "callback must return JSON object")
        if status_code == 200:
            self.assertIs(body.get("ok"), True, "HTTP 200 must acknowledge success")
        elif status_code in (400, 403, 409):
            self.assertIsInstance(body.get("error"), str,
                                  "rejected callback must return structured error")
        return status_code

    def state(self):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT status,result,updated_at FROM runner_commands WHERE id=?",
                (self.command_id,),
            ).fetchone()
            return tuple(row)

    def test_http_fixture_uses_isolated_paths(self):
        self.assertEqual(Path(self.tmp.name) / "history.db", server.DB)
        self.assertEqual(Path(self.tmp.name) / "project-layout.json", server.LAYOUT_FILE)
        self.assertTrue(server.DB.exists())
        self.assertTrue(self.thread.is_alive())

    def test_valid_receipt_updates_only_its_command(self):
        self.assertEqual(200, self.send(self.command_id, result="first accepted"))
        self.assertEqual(("completed", "first accepted"), self.state()[:2])

    def test_invalid_status_is_rejected_without_mutation(self):
        before = self.state()
        self.assertEqual(400, self.send(self.command_id, status="pending"))
        self.assertEqual(before, self.state())

    def test_unknown_positive_id_changes_no_rows(self):
        # This baseline tracks data integrity independently of the known
        # false-200 defect asserted in test_unknown_id_must_not_report_success.
        # Compare all persisted rows so a wrong-ID mutation cannot go unnoticed.
        with server.connect() as conn:
            before = [tuple(row) for row in conn.execute(
                "SELECT id,project_id,status,result,updated_at FROM runner_commands ORDER BY id"
            ).fetchall()]
        code = self.send(self.command_id + 50000, result="ghost")
        self.assertIn(code, (200, 409), "unknown ID must not trigger server error")
        with server.connect() as conn:
            after = [tuple(row) for row in conn.execute(
                "SELECT id,project_id,status,result,updated_at FROM runner_commands ORDER BY id"
            ).fetchall()]
        self.assertEqual(before, after)

    def test_known_defect_probe_has_working_http_transport(self):
        # A preflight outside expectedFailure prevents transport/DB/setup
        # failures from being mistaken for the known callback bug.
        self.assertEqual(200, self.send(self.command_id, result="preflight"))
        self.assertEqual(("completed", "preflight"), self.state()[:2])

    @unittest.expectedFailure
    def test_unsafe_javascript_integer_id_must_be_rejected(self):
        # SQLite can represent this integer, but JSON numeric transport through
        # browser Number cannot preserve it. Reject rather than risk ACKing an
        # adjacent command after the value has been rounded.
        unsafe_id = (1 << 53) + 1
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands "
                "(id,project_id,action,status,created_at,updated_at,result) "
                "VALUES (?, 'cloud', 'push', 'pending', '2026-10-09', '2026-10-09', NULL)",
                (unsafe_id,),
            )
        self.assertEqual(400, self.send(unsafe_id, result="unsafe"))
        with server.connect() as conn:
            row = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?", (unsafe_id,)
            ).fetchone()
        self.assertEqual(("pending", None), tuple(row))

    @unittest.expectedFailure
    def test_unknown_id_must_not_report_success(self):
        # Current endpoint returns {"ok":true} despite UPDATE rowcount=0.
        # Avoid claiming dispatch success for a command that never existed.
        self.assertEqual(409, self.send(self.command_id + 50000, result="ghost"))
        self.assertEqual(("pending", None), self.state()[:2])

    def test_malformed_status_rejection_preserves_pending_poll(self):
        before = self.state()
        self.assertEqual(400, self.send(self.command_id, status="completed;failed"))
        self.assertEqual(before, self.state())
        url = "http://127.0.0.1:%d/api/runner-commands" % self.http.server_port
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(200, response.status)
            commands = json.load(response)["commands"]
        self.assertIn(self.command_id, [row["id"] for row in commands])

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

    def test_failed_receipt_is_terminal_and_persists_error(self):
        self.assertEqual(200, self.send(self.command_id, status="failed", result="diagnostic"))
        self.assertEqual(("failed", "diagnostic"), self.state()[:2])

    def test_pending_get_excludes_acknowledged_command(self):
        url = "http://127.0.0.1:%d/api/runner-commands" % self.http.server_port
        self.assertEqual(200, self.send(self.command_id, result="acknowledged"))
        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(200, response.status)
            pending = json.load(response)["commands"]
        self.assertNotIn(self.command_id, [command["id"] for command in pending])

    def test_http_result_is_truncated_at_300_characters(self):
        payload = "R" * 301
        self.assertEqual(200, self.send(self.command_id, result=payload))
        self.assertEqual(("completed", "R" * 300), self.state()[:2])

    def test_result_null_and_nonstring_values_follow_existing_normalization(self):
        # Existing server endpoint uses str(value or '')[:300].
        # Capture this explicitly so the owner can choose whether to preserve
        # legacy semantics while introducing pending-only compare-and-swap.
        cases = ((None, ""), (0, ""), (False, ""), (123, "123"))
        for raw, expected in cases:
            with self.subTest(result=raw):
                with server.connect() as conn:
                    conn.execute(
                        "UPDATE runner_commands SET status='pending',result=NULL WHERE id=?",
                        (self.command_id,),
                    )
                self.assertEqual(200, self.send(self.command_id, result=raw))
                self.assertEqual(("completed", expected), self.state()[:2])

    def test_result_payload_does_not_execute_sql(self):
        injected = "x'); DELETE FROM runner_commands; --"
        with server.connect() as conn:
            before_count = conn.execute(
                "SELECT COUNT(*) FROM runner_commands"
            ).fetchone()[0]
        self.assertEqual(200, self.send(self.command_id, result=injected))
        self.assertEqual(("completed", injected), self.state()[:2])
        with server.connect() as conn:
            after_count = conn.execute(
                "SELECT COUNT(*) FROM runner_commands"
            ).fetchone()[0]
        self.assertEqual(before_count, after_count)

    def test_result_is_exactly_300_characters_at_boundary(self):
        boundary = "x" * 300
        self.assertEqual(200, self.send(self.command_id, result=boundary))
        self.assertEqual(("completed", boundary), self.state()[:2])

    def test_result_unicode_truncation_counts_python_characters(self):
        payload = "😀" * 301
        self.assertEqual(200, self.send(self.command_id, result=payload))
        self.assertEqual(("completed", "😀" * 300), self.state()[:2])

    def test_empty_result_does_not_change_status_contract(self):
        self.assertEqual(200, self.send(self.command_id, status="failed", result=""))
        self.assertEqual(("failed", ""), self.state()[:2])

    def test_rejected_status_preserves_all_command_rows(self):
        with server.connect() as conn:
            before = [tuple(row) for row in conn.execute(
                "SELECT id,status,result,updated_at FROM runner_commands ORDER BY id"
            ).fetchall()]
        self.assertEqual(400, self.send(self.command_id, status="cancelled", result="bad"))
        with server.connect() as conn:
            after = [tuple(row) for row in conn.execute(
                "SELECT id,status,result,updated_at FROM runner_commands ORDER BY id"
            ).fetchall()]
        self.assertEqual(before, after)

    def test_pending_poll_retains_other_commands_after_one_ack(self):
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands (project_id,action,status,created_at,updated_at,result)"
                " VALUES ('cloud','push','pending','2026-10-09','2026-10-09',NULL)"
            )
            other_id = conn.execute("SELECT max(id) FROM runner_commands").fetchone()[0]
        url = "http://127.0.0.1:%d/api/runner-commands" % self.http.server_port
        with urllib.request.urlopen(url, timeout=5) as response:
            before = {row["id"] for row in json.load(response)["commands"]}
        self.assertTrue({self.command_id, other_id}.issubset(before))
        self.assertEqual(200, self.send(self.command_id, result="one of two"))
        with urllib.request.urlopen(url, timeout=5) as response:
            after = {row["id"] for row in json.load(response)["commands"]}
        self.assertNotIn(self.command_id, after)
        self.assertIn(other_id, after)
        with server.connect() as conn:
            other_state = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?", (other_id,)
            ).fetchone()
        self.assertEqual(("pending", None), tuple(other_state))

    def test_receipt_only_mutates_target_command(self):
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands (project_id,action,status,created_at,updated_at,result)"
                " VALUES ('cloud','push','pending','2026-10-09','2026-10-09','untouched')"
            )
            other_id = conn.execute("SELECT max(id) FROM runner_commands").fetchone()[0]
            before = tuple(conn.execute(
                "SELECT status,result,updated_at FROM runner_commands WHERE id=?",
                (other_id,),
            ).fetchone())
        self.assertEqual(200, self.send(self.command_id, result="target only"))
        with server.connect() as conn:
            after = tuple(conn.execute(
                "SELECT status,result,updated_at FROM runner_commands WHERE id=?",
                (other_id,),
            ).fetchone())
        self.assertEqual(before, after)
        self.assertEqual(("completed", "target only"), self.state()[:2])

    def test_cross_project_receipt_does_not_modify_other_project(self):
        # zCloud's control-plane shares one command table across projects.
        # A cloud receipt must never mutate Supa's pending command.
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_commands (project_id,action,status,created_at,updated_at,result)"
                " VALUES ('supa','push','pending','2026-10-09','2026-10-09','private')"
            )
            other_id = conn.execute("SELECT max(id) FROM runner_commands").fetchone()[0]
            before = tuple(conn.execute(
                "SELECT project_id,status,result,updated_at FROM runner_commands WHERE id=?",
                (other_id,),
            ).fetchone())
        self.assertEqual(200, self.send(self.command_id, "completed", "cloud done"))
        with server.connect() as conn:
            after = tuple(conn.execute(
                "SELECT project_id,status,result,updated_at FROM runner_commands WHERE id=?",
                (other_id,),
            ).fetchone())
        self.assertEqual(before, after)
        self.assertEqual(("completed", "cloud done"), self.state()[:2])

    @unittest.expectedFailure
    def test_scheduler_expired_terminal_state_cannot_be_revived(self):
        # Simulate the watchdog marking an overdue command failed before
        # a worker's delayed successful HTTP receipt arrives.
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_commands SET status='failed',result='stale-timeout' "
                "WHERE id=?", (self.command_id,)
            )
        before = self.state()
        self.send(self.command_id, "completed", "late worker success")
        self.assertEqual(before, self.state())

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
    def test_negative_id_must_return_controlled_400(self):
        before = self.state()
        self.assertEqual(400, self.send(-1))
        self.assertEqual(before, self.state())

    @unittest.expectedFailure
    def test_numeric_string_id_must_not_coerce_to_command(self):
        before = self.state()
        self.assertEqual(400, self.send(str(self.command_id), result="coerced"))
        self.assertEqual(before, self.state())

    @unittest.expectedFailure
    def test_null_id_must_return_controlled_400(self):
        before = self.state()
        self.assertEqual(400, self.send(None))
        self.assertEqual(before, self.state())

    @unittest.expectedFailure
    def test_malformed_id_must_return_controlled_400(self):
        before = self.state()
        self.assertEqual(400, self.send("not-an-id"))
        self.assertEqual(before, self.state())

    @unittest.expectedFailure
    def test_zero_id_must_be_rejected_without_mutation(self):
        before = self.state()
        self.assertEqual(400, self.send(0))
        self.assertEqual(before, self.state())


if __name__ == "__main__":
    unittest.main()
