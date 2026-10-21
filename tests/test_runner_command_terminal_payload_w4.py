"""Offline regression for result truncation under duplicate terminal receipts.

Reference-model behavior only; production endpoint remains separately owned.
"""
import sqlite3
import unittest


class TerminalReceiptPayloadReference(unittest.TestCase):
    def test_first_payload_is_bounded_and_preserved_on_late_replay(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE runner_commands "
            "(id INTEGER PRIMARY KEY, status TEXT NOT NULL, result TEXT)"
        )
        db.execute("INSERT INTO runner_commands VALUES (1,'pending',NULL)")
        first_payload = "a" * 500
        first = db.execute(
            "UPDATE runner_commands SET status=?,result=? "
            "WHERE id=? AND status='pending'",
            ("completed", first_payload[:300], 1),
        )
        late = db.execute(
            "UPDATE runner_commands SET status=?,result=? "
            "WHERE id=? AND status='pending'",
            ("failed", "late callback", 1),
        )
        self.assertEqual(1, first.rowcount)
        self.assertEqual(0, late.rowcount)
        status, result = db.execute(
            "SELECT status,result FROM runner_commands WHERE id=1"
        ).fetchone()
        self.assertEqual("completed", status)
        self.assertEqual("a" * 300, result)
        self.assertEqual(300, len(result))


    def test_payload_length_boundaries(self):
        for length in (0, 1, 299, 300, 301, 16384):
            with self.subTest(length=length):
                db = sqlite3.connect(":memory:")
                try:
                    db.execute(
                        "CREATE TABLE runner_commands "
                        "(id INTEGER PRIMARY KEY, status TEXT NOT NULL, result TEXT)"
                    )
                    db.execute("INSERT INTO runner_commands VALUES(1,'pending',NULL)")
                    payload = "x" * length
                    update = db.execute(
                        "UPDATE runner_commands SET status=?,result=? "
                        "WHERE id=? AND status='pending'",
                        ("completed", payload[:300], 1),
                    )
                    self.assertEqual(1, update.rowcount)
                    saved = db.execute(
                        "SELECT result FROM runner_commands WHERE id=1"
                    ).fetchone()[0]
                    self.assertEqual("x" * min(length, 300), saved)
                finally:
                    db.close()

    def test_unicode_truncation_uses_character_count(self):
        payload = "🧪" * 350
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE runner_commands "
            "(id INTEGER PRIMARY KEY, status TEXT NOT NULL, result TEXT)"
        )
        db.execute("INSERT INTO runner_commands VALUES (1,'pending',NULL)")
        db.execute(
            "UPDATE runner_commands SET status=?,result=? "
            "WHERE id=? AND status='pending'",
            ("completed", payload[:300], 1),
        )
        stored = db.execute(
            "SELECT result FROM runner_commands WHERE id=1"
        ).fetchone()[0]
        self.assertEqual(300, len(stored))
        self.assertEqual("🧪" * 300, stored)

if __name__ == "__main__":
    unittest.main()
