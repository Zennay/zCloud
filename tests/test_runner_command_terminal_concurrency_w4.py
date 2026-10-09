"""Concurrency reference cases for idempotent runner command completion.

Execute without network or production database access. These are reference
semantics only; real HTTP/server concurrency remains an integration gate.
"""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path


def complete(path, barrier, outcome, status):
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        barrier.wait(timeout=5)
        result = connection.execute(
            "UPDATE runner_commands SET status=?,result=? "
            "WHERE id=1 AND status='pending'", (status, status)
        )
        outcome.append((status, result.rowcount))
    finally:
        connection.close()


class RunnerCommandConcurrencyReference(unittest.TestCase):
    def test_simultaneous_competing_terminal_receipts_have_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.db"
            with sqlite3.connect(path) as db:
                db.execute(
                    "CREATE TABLE runner_commands (id INTEGER PRIMARY KEY, "
                    "status TEXT NOT NULL, result TEXT)"
                )
                db.execute("INSERT INTO runner_commands VALUES (1,'pending',NULL)")
            barrier = threading.Barrier(3)
            outcome = []
            threads = [
                threading.Thread(target=complete, args=(path, barrier, outcome, status))
                for status in ("completed", "failed")
            ]
            for worker in threads:
                worker.start()
            barrier.wait(timeout=5)
            for worker in threads:
                worker.join(timeout=10)
                self.assertFalse(worker.is_alive(), "reference writer stalled")
            self.assertEqual(2, len(outcome))
            self.assertEqual([0, 1], sorted(count for _, count in outcome))
            with sqlite3.connect(path) as db:
                stored = db.execute(
                    "SELECT status,result FROM runner_commands WHERE id=1"
                ).fetchone()
            self.assertIn(stored, (("completed", "completed"), ("failed", "failed")))
            winner = next(status for status, count in outcome if count == 1)
            self.assertEqual(winner, stored[0])


if __name__ == "__main__":
    unittest.main()
