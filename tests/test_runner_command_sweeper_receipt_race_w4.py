"""Reference test: scheduler stale reconciliation and delayed receipts race.

No production database, server endpoints, or worker commands are accessed.
The expected first terminal writer wins; late callbacks cannot resurrect.
"""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path


def race_update(path, gate, status, value, results, errors):
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        gate.wait(timeout=5)
        row = db.execute(
            "UPDATE runner_commands SET status=?,result=? "
            "WHERE id=? AND status='pending'",
            (status, value, 1),
        )
        results.append((status, row.rowcount))
    except Exception as exc:
        errors.append(type(exc).__name__)
    finally:
        db.close()


class SchedulerReceiptRaceReference(unittest.TestCase):
    def test_stale_sweeper_and_callback_compete_without_resurrection(self):
        for _ in range(8):
            with self.subTest(iteration=_), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "commands.db"
                with sqlite3.connect(path) as db:
                    db.execute(
                        "CREATE TABLE runner_commands "
                        "(id INTEGER PRIMARY KEY, status TEXT NOT NULL, result TEXT)"
                    )
                    db.execute("INSERT INTO runner_commands VALUES(1,'pending',NULL)")
                gate = threading.Barrier(3)
                results = []
                errors = []
                threads = [
                    threading.Thread(
                        target=race_update,
                        args=(path, gate, "failed", "expired", results, errors),
                    ),
                    threading.Thread(
                        target=race_update,
                        args=(path, gate, "completed", "late ack", results, errors),
                    ),
                ]
                for worker in threads:
                    worker.start()
                gate.wait(timeout=5)
                for worker in threads:
                    worker.join(timeout=10)
                    self.assertFalse(worker.is_alive())
                self.assertEqual([], errors, "concurrent SQLite writer raised")
                self.assertEqual(2, len(results), "both writers must return")
                self.assertEqual([0, 1], sorted(count for _, count in results))
                with sqlite3.connect(path) as db:
                    status, value = db.execute(
                        "SELECT status,result FROM runner_commands WHERE id=1"
                    ).fetchone()
                self.assertIn((status, value), (("failed", "expired"), ("completed", "late ack")))
                winner = next(state for state, count in results if count == 1)
                self.assertEqual(winner, status)


if __name__ == "__main__":
    unittest.main()
