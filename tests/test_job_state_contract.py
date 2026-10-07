import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_job_state_contract import (
    CANONICAL_STATES,
    audit_job_state_contract,
    canonical_job_state,
)


SERVER_SOURCE = """
def portfolio_queue_finish(global_slot, queue_id, result, evidence='', next_task=None):
    result = str(result or '').strip().upper()
    if result not in ('DONE','BLOCKED','CONTINUE'):
        raise ValueError('invalid')
    if result == 'DONE':
        sql = "UPDATE portfolio_queue SET status='done' WHERE queue_id=?"
    elif result == 'BLOCKED':
        sql = "UPDATE portfolio_queue SET status='dropped' WHERE queue_id=?"
    else:
        sql = "UPDATE portfolio_queue SET status='queued' WHERE queue_id=?"
    return sql
"""


class JobStateContractTests(unittest.TestCase):
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
                    blocker TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, status, *, blocker="", attempts=0):
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,status,blocker,attempts) VALUES(?,?,?,?)",
                (f"q-{status}-{attempts}-{blocker != ''}", status, blocker, attempts),
            )

    def test_canonical_mapping_covers_native_and_legacy_states(self):
        self.assertEqual("QUEUED", canonical_job_state("queued"))
        self.assertEqual("RUNNING", canonical_job_state("running"))
        self.assertEqual("RUNNING", canonical_job_state("claimed"))
        self.assertEqual("RUNNING", canonical_job_state("verifying"))
        self.assertEqual("DONE", canonical_job_state("done"))
        self.assertEqual("BLOCKED", canonical_job_state("dropped"))
        self.assertEqual("BLOCKED", canonical_job_state("blocked"))
        self.assertEqual("NEEDS_AI", canonical_job_state("needs_ai"))
        self.assertEqual(
            "FAILED_RETRYABLE",
            canonical_job_state("failed", attempts=2, retry_limit=3),
        )
        self.assertEqual(
            "FAILED_FINAL",
            canonical_job_state("failed", attempts=3, retry_limit=3),
        )
        self.assertEqual("FAILED_FINAL", canonical_job_state("failed_final"))

    def test_unknown_state_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            canonical_job_state("mystery-state")
        with self.assertRaisesRegex(ValueError, "integer attempts"):
            canonical_job_state("failed", attempts="not-an-int")

    def test_read_only_audit_reports_legacy_gap_without_payloads(self):
        self.insert("queued")
        self.insert("claimed")
        self.insert("verifying")
        self.insert("done")
        self.insert("dropped", blocker="human approval required")

        before = (self.db.stat().st_size, self.db.stat().st_mtime_ns)
        report = audit_job_state_contract(self.db, self.source)
        after = (self.db.stat().st_size, self.db.stat().st_mtime_ns)

        self.assertEqual(before, after)
        self.assertTrue(report["database_fingerprint_unchanged"])
        self.assertTrue(report["mapping_complete"])
        self.assertFalse(report["native_lifecycle_complete"])
        self.assertEqual(5, report["rows_observed"])
        self.assertEqual(2, report["canonical_state_counts"]["RUNNING"])
        self.assertEqual(1, report["canonical_state_counts"]["BLOCKED"])
        self.assertEqual(3, report["legacy_mapped_rows"])
        self.assertEqual(0, report["unknown_status_rows"])
        self.assertEqual(
            [
                "blocked_collapsed_to_legacy_storage",
                "needs_ai_transition_missing",
                "failed_retryable_transition_missing",
                "failed_final_transition_missing",
            ],
            report["source_contract"]["gap_codes"],
        )
        rendered = repr(report)
        self.assertNotIn("human approval required", rendered)
        self.assertEqual(set(CANONICAL_STATES), set(report["canonical_states"]))

    def test_future_native_contract_can_be_reported_complete(self):
        native_source = self.source.parent / "native_server.py"
        native_source.write_text(
            """
def portfolio_queue_finish(global_slot, queue_id, result, evidence='', next_task=None):
    result = str(result or '').strip().upper()
    if result not in (
        'DONE','BLOCKED','CONTINUE','NEEDS_AI','FAILED_RETRYABLE','FAILED_FINAL'
    ):
        raise ValueError('invalid')
    sql = {
        'DONE': "UPDATE portfolio_queue SET status='done'",
        'BLOCKED': "UPDATE portfolio_queue SET status='blocked'",
        'CONTINUE': "UPDATE portfolio_queue SET status='queued'",
        'NEEDS_AI': "UPDATE portfolio_queue SET status='needs_ai'",
        'FAILED_RETRYABLE': "UPDATE portfolio_queue SET status='failed_retryable'",
        'FAILED_FINAL': "UPDATE portfolio_queue SET status='failed_final'",
    }[result]
    return sql
""",
            encoding="utf-8",
        )
        for status in (
            "queued",
            "running",
            "done",
            "blocked",
            "needs_ai",
            "failed_retryable",
            "failed_final",
        ):
            self.insert(status)

        report = audit_job_state_contract(self.db, native_source)
        self.assertTrue(report["native_lifecycle_complete"])
        self.assertEqual([], report["source_contract"]["gap_codes"])

    def test_unknown_database_state_is_counted_without_leaking_value(self):
        self.insert("customer-specific-secret-status")
        report = audit_job_state_contract(self.db, self.source)
        self.assertFalse(report["mapping_complete"])
        self.assertEqual(1, report["unknown_status_rows"])
        self.assertNotIn("customer-specific-secret-status", repr(report))

    def test_symlink_database_is_rejected(self):
        link = self.db.parent / "db-link"
        try:
            link.symlink_to(self.db)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            audit_job_state_contract(link, self.source)

    def test_missing_finish_function_fails_closed(self):
        source = self.source.parent / "missing.py"
        source.write_text("def other():\n    return 1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "portfolio_queue_finish"):
            audit_job_state_contract(self.db, source)


if __name__ == "__main__":
    unittest.main()
