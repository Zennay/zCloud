import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_needs_ai_packet_contract import (
    audit_needs_ai_coverage,
    validate_needs_ai_packet,
)


def valid_packet():
    return {
        "schema_version": 1,
        "task": "Recover deterministic worker dispatch",
        "last_good_step": "Exact-head regression completed",
        "revision": "abcdef1234567890",
        "retries": 2,
        "logs": ["artifact://workflow/123/job/456"],
        "evidence": {"run_id": 123, "health": "green-before-failure"},
        "blocker": "Novel runner transition failed after bounded retry",
        "decision_needed": "Choose whether to retry with a fresh worker mapping",
    }


class NeedsAiPacketContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """
                CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ci_status TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert_receipt(self, *, ci_status="failure", blocker="blocked", evidence=None):
        payload = "{}" if evidence is None else json.dumps(evidence)
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """
                INSERT INTO project_state_receipts(ci_status,blocker,evidence_json)
                VALUES(?,?,?)
                """,
                (ci_status, blocker, payload),
            )

    def test_valid_packet_is_accepted(self):
        result = validate_needs_ai_packet(valid_packet())
        self.assertTrue(result["valid"])
        self.assertEqual([], result["error_codes"])

    def test_missing_and_malformed_fields_fail_closed_without_payload_echo(self):
        packet = valid_packet()
        packet.pop("decision_needed")
        packet["revision"] = "contains spaces and secret text"
        packet["retries"] = True
        packet["logs"] = []
        result = validate_needs_ai_packet(packet)

        self.assertFalse(result["valid"])
        self.assertIn("missing_required_fields", result["error_codes"])
        self.assertIn("invalid_revision", result["error_codes"])
        self.assertIn("invalid_retries", result["error_codes"])
        self.assertIn("invalid_logs", result["error_codes"])
        self.assertNotIn("secret text", repr(result))

    def test_packet_and_evidence_bounds_are_enforced(self):
        packet = valid_packet()
        packet["evidence"] = {f"k{i}": i for i in range(21)}
        result = validate_needs_ai_packet(packet)
        self.assertFalse(result["valid"])
        self.assertIn("evidence_too_many_keys", result["error_codes"])

        packet = valid_packet()
        packet["logs"] = ["x" * 501]
        result = validate_needs_ai_packet(packet)
        self.assertFalse(result["valid"])
        self.assertIn("invalid_logs", result["error_codes"])

    def test_read_only_coverage_audit_counts_packets_without_leaking_them(self):
        private_marker = "do-not-emit-this-blocker"
        self.insert_receipt(
            blocker=private_marker,
            evidence={"needs_ai_packet": valid_packet()},
        )
        self.insert_receipt(evidence={"other": "receipt"})
        self.insert_receipt(ci_status="success", blocker="", evidence={"ignored": True})

        before = (self.db.stat().st_size, self.db.stat().st_mtime_ns)
        report = audit_needs_ai_coverage(self.db)
        after = (self.db.stat().st_size, self.db.stat().st_mtime_ns)

        self.assertEqual(before, after)
        self.assertTrue(report["database_fingerprint_unchanged"])
        self.assertEqual(2, report["failure_receipts_observed"])
        self.assertEqual(1, report["packets_present"])
        self.assertEqual(1, report["valid_packets"])
        self.assertEqual(1, report["missing_packets"])
        self.assertEqual("partial", report["coverage_state"])
        self.assertFalse(report["coverage_complete"])
        self.assertNotIn(private_marker, repr(report))
        self.assertNotIn("Recover deterministic worker dispatch", repr(report))

    def test_complete_coverage_is_detected(self):
        self.insert_receipt(evidence={"needs_ai_packet": valid_packet()})
        packet = valid_packet()
        packet["revision"] = "1234567"
        packet["retries"] = 0
        self.insert_receipt(evidence={"needs_ai_packet": packet})

        report = audit_needs_ai_coverage(self.db)
        self.assertTrue(report["coverage_complete"])
        self.assertEqual("complete", report["coverage_state"])
        self.assertEqual(2, report["valid_packets"])

    def test_invalid_packet_errors_are_aggregated_only(self):
        packet = valid_packet()
        packet["decision_needed"] = ""
        packet["retries"] = -1
        self.insert_receipt(evidence={"needs_ai_packet": packet})

        report = audit_needs_ai_coverage(self.db)
        self.assertEqual(1, report["invalid_packets"])
        self.assertEqual(
            {"invalid_decision_needed": 1, "invalid_retries": 1},
            report["validation_error_counts"],
        )
        self.assertNotIn("Novel runner transition", repr(report))

    def test_malformed_evidence_is_counted_without_raw_value(self):
        marker = "not-json-private-marker"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """
                INSERT INTO project_state_receipts(ci_status,blocker,evidence_json)
                VALUES('failure','blocked',?)
                """,
                (marker,),
            )

        report = audit_needs_ai_coverage(self.db)
        self.assertEqual(1, report["malformed_evidence_rows"])
        self.assertEqual(1, report["missing_packets"])
        self.assertNotIn(marker, repr(report))

    def test_no_failure_receipts_is_explicit(self):
        report = audit_needs_ai_coverage(self.db)
        self.assertEqual("no_failure_receipts", report["coverage_state"])
        self.assertFalse(report["coverage_complete"])

    def test_symlink_database_is_rejected(self):
        link = self.db.parent / "history-link.db"
        try:
            link.symlink_to(self.db)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            audit_needs_ai_coverage(link)

    def test_limit_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            audit_needs_ai_coverage(self.db, limit=0)
        with self.assertRaisesRegex(ValueError, "limit"):
            audit_needs_ai_coverage(self.db, limit=2001)


if __name__ == "__main__":
    unittest.main()
