"""Offline SQLite identifier-equivalence probes; no zCloud runtime or production DB imports.

This contract tests database key semantics only. HTTP admission and visual
rendering remain unverified until isolated handler fixtures are approved.
"""
import sqlite3
import unittest
import unicodedata


SCHEMA = """CREATE TABLE claims (
    project_id TEXT NOT NULL,
    claim_key TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY(project_id, claim_key)
)"""


class IdentifierEquivalenceContract(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(SCHEMA)

    def tearDown(self):
        self.db.close()

    def test_confusable_pairs_are_distinct_raw_sqlite_keys(self):
        pairs = [
            ("notion:é", "notion:e\u0301"),
            ("task:a", "task:\u0430"),  # Cyrillic small a
            ("task:xy", "task:x\u202ey"),  # bidi override
            ("notion:api", "notion\uff1aapi"),
            ("task:Ready", "task:ready"),
            ("task:ready", "task:ready "),
            ("task:xy", "task:x\u200dy"),
        ]
        for first, second in pairs:
            with self.subTest(first=repr(first), second=repr(second)):
                self.assertNotEqual(first, second)
                self.db.execute("DELETE FROM claims")
                self.db.execute("INSERT INTO claims VALUES (?, ?, ?)", ("cloud", first, "owner-a"))
                self.db.execute("INSERT INTO claims VALUES (?, ?, ?)", ("cloud", second, "owner-b"))
                rows = self.db.execute(
                    "SELECT claim_key, owner_id FROM claims WHERE project_id = ?", ("cloud",)
                ).fetchall()
                self.assertEqual({(first, "owner-a"), (second, "owner-b")}, set(rows))
                self.db.execute(
                    "DELETE FROM claims WHERE project_id=? AND claim_key=? AND owner_id=?",
                    ("cloud", first, "owner-b"),
                )
                self.assertEqual(2, self.db.execute("SELECT count(*) FROM claims").fetchone()[0])

    def test_project_namespace_prevents_cross_project_release(self):
        for project, owner in (("cloud", "cloud-owner"), ("cloud::w1", "worker-owner")):
            self.db.execute("INSERT INTO claims VALUES (?, ?, ?)", (project, "notion:api", owner))
        self.db.execute(
            "DELETE FROM claims WHERE project_id=? AND claim_key=? AND owner_id=?",
            ("cloud::w1", "notion:api", "cloud-owner"),
        )
        self.assertEqual(2, self.db.execute("SELECT count(*) FROM claims").fetchone()[0])
        self.db.execute(
            "DELETE FROM claims WHERE project_id=? AND claim_key=? AND owner_id=?",
            ("cloud::w1", "notion:api", "worker-owner"),
        )
        self.assertEqual([("cloud", "notion:api", "cloud-owner")],
                         self.db.execute("SELECT * FROM claims").fetchall())

    def test_normalization_would_collapse_an_existing_pair(self):
        first, second = "notion:é", "notion:e\u0301"
        self.assertNotEqual(first, second)
        self.assertEqual(unicodedata.normalize("NFC", first),
                         unicodedata.normalize("NFC", second))


if __name__ == "__main__":
    unittest.main()
