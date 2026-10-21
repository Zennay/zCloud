"""Offline differential contract tests for proposed claim identifier identity (issue #1228).

These tests intentionally do NOT import server.py or access production SQLite.
They verify fixture integrity and exact tuple separation only; they do not
assert the current HTTP endpoint already enforces the proposed policy.
"""
import json
import pathlib
import sqlite3
import unittest
import unicodedata

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "task_claim_identity_cases_20261009.json"


class ClaimIdentityOfflineCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]

    def test_unique_identifiers_and_expected_categories(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 10)
        self.assertIn("ascii-composite", ids)
        self.assertIn("json-unpaired-surrogate", ids)

    def test_exact_tuple_keys_remain_distinct_in_temporary_sqlite(self):
        for case in self.cases:
            if case["class"] != "distinct-not-equivalent":
                continue
            with self.subTest(case=case["id"]):
                first = (case["project"], case["key"])
                second = (
                    case.get("compare_project", case["project"]),
                    case["compare_key"],
                )
                self.assertNotEqual(first, second)
                connection = sqlite3.connect(":memory:")
                try:
                    connection.execute(
                        "CREATE TABLE claims (project TEXT, claim_key TEXT, owner TEXT, "
                        "PRIMARY KEY(project, claim_key))"
                    )
                    connection.execute("INSERT INTO claims VALUES (?, ?, ?)", (*first, "owner-a"))
                    connection.execute("INSERT INTO claims VALUES (?, ?, ?)", (*second, "owner-b"))
                    self.assertEqual(
                        connection.execute("SELECT count(*) FROM claims").fetchone()[0], 2
                    )
                    connection.execute(
                        "DELETE FROM claims WHERE project=? AND claim_key=? AND owner=?",
                        (*first, "owner-a"),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT owner FROM claims WHERE project=? AND claim_key=?", second
                        ).fetchone()[0],
                        "owner-b",
                    )
                finally:
                    connection.close()

    def test_proposed_nfc_rejection_is_not_silent_normalization(self):
        by_id = {case["id"]: case for case in self.cases}
        nfd = by_id["nfd-combining"]["key"]
        nfc = by_id["nfc-precomposed"]["key"]
        self.assertNotEqual(nfd, nfc)
        self.assertEqual(unicodedata.normalize("NFC", nfd), nfc)
        self.assertNotEqual(unicodedata.normalize("NFC", nfd), nfd)

    def test_invalid_surrogate_is_represented_as_raw_json_fixture(self):
        case = next(c for c in self.cases if c["id"] == "json-unpaired-surrogate")
        payload = json.loads(case["raw_json"])
        self.assertEqual(payload["claim_key"], "\ud800")
        with self.assertRaises(UnicodeEncodeError):
            payload["claim_key"].encode("utf-8", errors="strict")

    def test_dangerous_format_characters_are_explicit_fixtures(self):
        by_id = {case["id"]: case for case in self.cases}
        for case_id in ("bidi-override", "zero-width"):
            self.assertTrue(
                any(unicodedata.category(c) == "Cf" for c in by_id[case_id]["key"])
            )

    def test_proposal_marker_prevents_claiming_runtime_enforcement(self):
        manifest = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertIn("proposal-only", manifest["status"])


if __name__ == "__main__":
    unittest.main()
