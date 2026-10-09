"""Offline SQLite identifier-equivalence probes; no zCloud runtime or production DB imports.

This contract tests database key semantics only. HTTP admission and visual
rendering remain unverified until isolated handler fixtures are approved.
"""
import json
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

    def test_json_round_trip_preserves_machine_identifiers(self):
        keys = [
            "notion:é", "notion:e\\u0301", "task:\\u0430",
            "task:x\\u202ey", "task:x\\u2066y", "task:x\\u200dy",
            "notion\\uff1aapi",
        ]
        for key in keys:
            with self.subTest(key=repr(key)):
                wire = json.dumps({"project_id": "cloud", "claim_key": key}, ensure_ascii=True)
                decoded = json.loads(wire)
                self.assertEqual(key, decoded["claim_key"])
                self.db.execute("INSERT INTO claims VALUES (?, ?, ?)", ("cloud", decoded["claim_key"], "owner-a"))
                stored = self.db.execute(
                    "SELECT claim_key FROM claims WHERE project_id=? AND claim_key=?",
                    ("cloud", key),
                ).fetchone()[0]
                self.assertEqual(key, stored)
                self.db.execute("DELETE FROM claims")

    def test_owner_mismatch_never_deletes_unicode_claim(self):
        for claim_key in ("notion:é", "notion:e\\u0301", "task:\\u202e", "notion\\uff1aapi"):
            with self.subTest(key=repr(claim_key)):
                self.db.execute("INSERT INTO claims VALUES (?, ?, ?)", ("cloud", claim_key, "owner-a"))
                self.db.execute(
                    "DELETE FROM claims WHERE project_id=? AND claim_key=? AND owner_id=?",
                    ("cloud", claim_key, "owner-b"),
                )
                self.assertEqual(("owner-a",), self.db.execute(
                    "SELECT owner_id FROM claims WHERE project_id=? AND claim_key=?",
                    ("cloud", claim_key),
                ).fetchone())
                self.db.execute("DELETE FROM claims")

    def test_normalization_would_collapse_an_existing_pair(self):
        first, second = "notion:é", "notion:e\u0301"
        self.assertNotEqual(first, second)
        self.assertEqual(unicodedata.normalize("NFC", first),
                         unicodedata.normalize("NFC", second))


if __name__ == "__main__":
    unittest.main()
