"""Offline SQLite reference cases for runner command terminal-state fencing.

These tests are GREEN reference semantics, not server API conformance.
They do not open or modify the live history.db or production services.
"""
import sqlite3
import unittest


def fixture():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE runner_commands (id INTEGER PRIMARY KEY, status TEXT NOT NULL, result TEXT)")
    db.executemany(
        "INSERT INTO runner_commands (id,status,result) VALUES (?,?,?)",
        [(1, "pending", None), (2, "completed", "original"), (3, "failed", "stale")],
    )
    return db


def finish(db, command_id, status, result):
    if type(command_id) is not int or command_id <= 0:
        return False
    if status not in ("completed", "failed"):
        return False
    with db:
        result_update = db.execute(
            "UPDATE runner_commands SET status=?, result=? "
            "WHERE id=? AND status='pending'",
            (status, result, command_id),
        )
    return result_update.rowcount == 1


class RunnerTerminalReferenceModel(unittest.TestCase):
    def setUp(self):
        self.db = fixture()

    def tearDown(self):
        self.db.close()

    def row(self, command_id):
        return self.db.execute(
            "SELECT status,result FROM runner_commands WHERE id=?", (command_id,)
        ).fetchone()

    def test_pending_completes_exactly_once(self):
        self.assertTrue(finish(self.db, 1, "completed", "ack"))
        self.assertFalse(finish(self.db, 1, "failed", "delayed error"))
        self.assertEqual(("completed", "ack"), self.row(1))

    def test_stale_failed_command_cannot_resurrect(self):
        self.assertFalse(finish(self.db, 3, "completed", "late ack"))
        self.assertEqual(("failed", "stale"), self.row(3))

    def test_existing_success_cannot_be_overwritten(self):
        self.assertFalse(finish(self.db, 2, "failed", "late failure"))
        self.assertEqual(("completed", "original"), self.row(2))

    def test_unknown_id_never_creates_command(self):
        self.assertFalse(finish(self.db, 999, "completed", "ghost"))
        self.assertIsNone(self.row(999))

    def test_invalid_ids_fail_closed(self):
        for bad_id in (0, -1, True, "1", None):
            with self.subTest(bad_id=bad_id):
                self.assertFalse(finish(self.db, bad_id, "completed", "invalid"))
        self.assertEqual(("pending", None), self.row(1))

    def test_invalid_status_does_not_finalize(self):
        self.assertFalse(finish(self.db, 1, "pending", "replay"))
        self.assertFalse(finish(self.db, 1, "cancelled", "unexpected"))
        self.assertEqual(("pending", None), self.row(1))

    def test_separate_commands_do_not_interfere(self):
        self.db.execute("INSERT INTO runner_commands VALUES (4,'pending',NULL)")
        self.assertTrue(finish(self.db, 4, "failed", "different"))
        self.assertEqual(("pending", None), self.row(1))


    def test_javascript_number_precision_collision_requires_transport_gate(self):
        # JavaScript JSON.parse uses IEEE-754 Number for numeric command IDs.
        # SQLite can allocate IDs beyond 2**53 - 1. This test documents
        # the precision collision; it does not claim production validation.
        safe_limit = 2**53 - 1
        self.assertEqual(safe_limit, int(float(safe_limit)))
        unsafe_id = safe_limit + 2
        self.assertNotEqual(unsafe_id, int(float(unsafe_id)))
        self.assertEqual(int(float(unsafe_id)), int(float(unsafe_id - 1)))

    def test_sqlite_accepts_ids_beyond_javascript_safe_range(self):
        unsafe_id = 2**53 + 1
        self.db.execute(
            "INSERT INTO runner_commands (id,status,result) VALUES (?,?,?)",
            (unsafe_id, "pending", None),
        )
        self.assertTrue(finish(self.db, unsafe_id, "completed", "server-only"))
        self.assertEqual(("completed", "server-only"), self.row(unsafe_id))

    def test_sql_injection_like_result_remains_inert_data(self):
        payload = "x'); DELETE FROM runner_commands; --"
        self.assertTrue(finish(self.db, 1, "completed", payload))
        self.assertEqual(("completed", payload), self.row(1))
        self.assertEqual(3, self.db.execute("SELECT COUNT(*) FROM runner_commands").fetchone()[0])

    def test_cross_command_replay_does_not_change_unrelated_terminal_result(self):
        self.assertTrue(finish(self.db, 1, "failed", "first"))
        self.assertFalse(finish(self.db, 2, "completed", "late cross-command"))
        self.assertEqual(("failed", "first"), self.row(1))
        self.assertEqual(("completed", "original"), self.row(2))

if __name__ == "__main__":
    unittest.main()
