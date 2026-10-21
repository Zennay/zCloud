import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_correlation_coverage import audit_correlation_coverage


class CorrelationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"

    def tearDown(self):
        self.tmp.cleanup()

    def make_db(self, *, runner_tokens=False):
        with sqlite3.connect(self.db) as connection:
            connection.executescript(
                """
                CREATE TABLE portfolio_queue(
                    queue_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                );
                CREATE TABLE task_claims(
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                );
                """
            )
            if runner_tokens:
                connection.executescript(
                    """
                    CREATE TABLE runner_commands(
                        id INTEGER PRIMARY KEY,
                        queue_id TEXT,
                        project_id TEXT,
                        action TEXT,
                        status TEXT
                    );
                    CREATE TABLE runner_events(
                        id INTEGER PRIMARY KEY,
                        queue_id TEXT,
                        event TEXT,
                        project_id TEXT,
                        worker_slot INTEGER
                    );
                    """
                )
            else:
                connection.executescript(
                    """
                    CREATE TABLE runner_commands(
                        id INTEGER PRIMARY KEY,
                        project_id TEXT,
                        action TEXT,
                        status TEXT
                    );
                    CREATE TABLE runner_events(
                        id INTEGER PRIMARY KEY,
                        event TEXT,
                        project_id TEXT,
                        worker_slot INTEGER
                    );
                    """
                )
            connection.executemany(
                "INSERT INTO portfolio_queue(queue_id,status) VALUES(?,?)",
                [("q1", "running"), ("q2", "done")],
            )
            connection.execute(
                "INSERT INTO task_claims(metadata_json) VALUES(?)",
                (json.dumps({"queue_id": "q1"}),),
            )
            connection.execute(
                "INSERT INTO project_state_receipts(source,evidence_json) VALUES(?,?)",
                ("portfolio_queue:q2", "{}"),
            )
            if runner_tokens:
                connection.execute(
                    "INSERT INTO runner_commands(queue_id,project_id,action,status) VALUES(?,?,?,?)",
                    ("q1", "cloud::w1", "push", "completed"),
                )
                connection.execute(
                    "INSERT INTO runner_events(queue_id,event,project_id,worker_slot) VALUES(?,?,?,?)",
                    ("q1", "prompt-sent", "cloud", 1),
                )

    def test_current_schema_reports_exact_gap_without_guessing(self):
        self.make_db()
        before = self.db.read_bytes()
        report = audit_correlation_coverage(self.db)
        after = self.db.read_bytes()

        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])
        self.assertTrue(report["database_fingerprint_unchanged"])
        self.assertEqual(2, report["queue_items_examined"])
        self.assertEqual(
            {"linked_items": 1, "missing_items": 1},
            report["links"]["queue_to_claim"],
        )
        self.assertEqual(
            {"linked_items": 1, "missing_items": 1},
            report["links"]["queue_to_receipt"],
        )
        self.assertIsNone(report["links"]["runner_commands"]["token_column"])
        self.assertIsNone(report["links"]["runner_events"]["token_column"])
        self.assertFalse(report["end_to_end_ready"])
        self.assertIn(
            "runner_commands_missing_queue_or_correlation_id",
            report["reason_codes"],
        )
        self.assertIn(
            "runner_events_missing_queue_or_correlation_id",
            report["reason_codes"],
        )

    def test_exact_queue_token_in_commands_and_events_can_be_proven(self):
        self.make_db(runner_tokens=True)
        report = audit_correlation_coverage(self.db)

        self.assertEqual("queue_id", report["common_runner_token_column"])
        self.assertEqual("queue_id", report["links"]["runner_commands"]["token_column"])
        self.assertEqual("queue_id", report["links"]["runner_events"]["token_column"])
        self.assertEqual(1, report["links"]["runner_commands"]["linked_rows"])
        self.assertEqual(1, report["links"]["runner_events"]["linked_rows"])
        self.assertTrue(report["end_to_end_ready"])

    def test_malformed_metadata_is_counted_not_leaked(self):
        self.make_db()
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO task_claims(metadata_json) VALUES(?)",
                ('{"queue_id":',),
            )
            connection.execute(
                "INSERT INTO project_state_receipts(source,evidence_json) VALUES(?,?)",
                ("manual", '{"queue_id":'),
            )

        report = audit_correlation_coverage(self.db)
        self.assertIn("malformed_claim_metadata", report["reason_codes"])
        self.assertIn("malformed_receipt_evidence", report["reason_codes"])
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("q1", serialized)
        self.assertNotIn("q2", serialized)

    def test_missing_required_table_fails_closed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE portfolio_queue(queue_id TEXT,status TEXT)")

        with self.assertRaisesRegex(ValueError, "missing required correlation tables"):
            audit_correlation_coverage(self.db)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_database_is_rejected(self):
        self.make_db()
        link = self.root / "history-link.db"
        os.symlink(self.db, link)

        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            audit_correlation_coverage(link)


if __name__ == "__main__":
    unittest.main()
