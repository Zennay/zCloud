import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_retry_policy_contract import (
    audit_retry_readiness,
    retry_disposition,
)


SERVER_SOURCE = """
def portfolio_queue_allocate():
    sql = "UPDATE portfolio_queue SET attempts=attempts+1 WHERE queue_id=?"
    return sql

def portfolio_queue_finish(global_slot, queue_id, result, evidence='', next_task=None):
    result = str(result or '').strip().upper()
    if result not in ('DONE','BLOCKED','CONTINUE'):
        raise ValueError('invalid')
    return result
"""


class RetryPolicyContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "history.db"
        self.source = root / "server.py"
        self.source.write_text(SERVER_SOURCE, encoding="utf-8")
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """
                CREATE TABLE portfolio_queue(
                    queue_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, status, attempts):
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,status,attempts) VALUES(?,?,?)",
                (f"q-{status}-{attempts}-{id(self)}", status, attempts),
            )

    def test_transient_failures_retry_with_bounded_exponential_backoff(self):
        first = retry_disposition("transient", 1)
        second = retry_disposition("transient", 2)
        exhausted = retry_disposition("transient", 3)

        self.assertEqual("FAILED_RETRYABLE", first["disposition"])
        self.assertEqual(30, first["delay_seconds"])
        self.assertTrue(first["retry"])
        self.assertEqual("FAILED_RETRYABLE", second["disposition"])
        self.assertEqual(60, second["delay_seconds"])
        self.assertEqual("NEEDS_AI", exhausted["disposition"])
        self.assertFalse(exhausted["retry"])
        self.assertEqual("retry_budget_exhausted", exhausted["reason_code"])

        capped = retry_disposition(
            "transient",
            4,
            max_attempts=8,
            base_delay_seconds=300,
            max_delay_seconds=900,
        )
        self.assertEqual(900, capped["delay_seconds"])

    def test_novel_and_terminal_failures_do_not_retry(self):
        novel = retry_disposition("novel", 1)
        terminal = retry_disposition("terminal", 1)

        self.assertEqual("NEEDS_AI", novel["disposition"])
        self.assertFalse(novel["retry"])
        self.assertEqual("FAILED_FINAL", terminal["disposition"])
        self.assertFalse(terminal["retry"])

    def test_invalid_policy_inputs_fail_closed(self):
        for attempts in (-1, True, "not-int"):
            with self.assertRaises(ValueError):
                retry_disposition("transient", attempts)
        with self.assertRaisesRegex(ValueError, "failure class"):
            retry_disposition("unknown", 1)
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            retry_disposition("transient", 1, max_attempts=0)
        with self.assertRaisesRegex(ValueError, "backoff"):
            retry_disposition(
                "transient",
                1,
                base_delay_seconds=60,
                max_delay_seconds=30,
            )

    def test_read_only_audit_reports_attempt_pressure_and_writer_gaps(self):
        self.insert("queued", 0)
        self.insert("claimed", 1)
        self.insert("running", 3)
        self.insert("done", 5)

        before = (self.db.stat().st_size, self.db.stat().st_mtime_ns)
        report = audit_retry_readiness(self.db, self.source, max_attempts=3)
        after = (self.db.stat().st_size, self.db.stat().st_mtime_ns)

        self.assertEqual(before, after)
        self.assertTrue(report["database_fingerprint_unchanged"])
        self.assertEqual(4, report["rows_observed"])
        self.assertEqual({"0": 1, "1": 1, "2": 0, "3+": 2}, report["attempt_buckets"])
        self.assertEqual(3, report["active_rows"])
        self.assertEqual(1, report["active_rows_at_or_over_retry_budget"])
        self.assertEqual(5, report["max_attempts_observed"])
        self.assertFalse(report["writer_retry_contract_complete"])
        self.assertTrue(
            report["source_contract"]["attempt_counter_increment_present"]
        )
        self.assertEqual(
            [
                "failed_retryable_transition_missing",
                "needs_ai_transition_missing",
                "failed_final_transition_missing",
                "retry_backoff_marker_missing",
                "failure_classification_marker_missing",
            ],
            report["source_contract"]["gap_codes"],
        )

    def test_future_writer_contract_can_be_detected_complete(self):
        source = self.source.parent / "future.py"
        source.write_text(
            """
def portfolio_queue_allocate():
    retry_after = 'retry_after'
    failure_class = 'failure_class'
    sql = "UPDATE portfolio_queue SET attempts=attempts+1 WHERE queue_id=?"
    return retry_after, failure_class, sql

def portfolio_queue_finish(global_slot, queue_id, result, evidence='', next_task=None):
    if result not in (
        'FAILED_RETRYABLE','NEEDS_AI','FAILED_FINAL','DONE','BLOCKED','CONTINUE'
    ):
        raise ValueError('invalid')
    return result
""",
            encoding="utf-8",
        )
        self.insert("queued", 0)
        report = audit_retry_readiness(self.db, source)
        self.assertTrue(report["writer_retry_contract_complete"])
        self.assertEqual([], report["source_contract"]["gap_codes"])

    def test_symlink_inputs_are_rejected(self):
        link = self.db.parent / "history-link.db"
        try:
            link.symlink_to(self.db)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            audit_retry_readiness(link, self.source)

    def test_missing_queue_table_fails_closed(self):
        other = self.db.parent / "other.db"
        sqlite3.connect(other).close()
        with self.assertRaisesRegex(ValueError, "portfolio_queue"):
            audit_retry_readiness(other, self.source)


if __name__ == "__main__":
    unittest.main()
