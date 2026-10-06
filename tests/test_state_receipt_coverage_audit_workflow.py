from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import project_runtime


ROOT = Path(__file__).resolve().parents[1]


class StateReceiptCoverageAuditWorkflowTests(unittest.TestCase):
    def workflow_text(self) -> str:
        return (
            ROOT / ".github" / "workflows" / "zcloud-state-receipt-coverage-audit.yml"
        ).read_text(encoding="utf-8")

    def test_audit_reads_vps_source_of_truth_without_dashboard_http_dependency(self):
        text = self.workflow_text()

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('Path("/home/ubuntu/zennay-cloud")', text)
        self.assertIn('"projects.json"', text)
        self.assertIn('"history.db"', text)
        self.assertIn("project_runtime.receipt_coverage(", text)
        self.assertIn("project_runtime.latest_receipts(", text)
        self.assertIn("max_age_seconds=7200", text)
        self.assertIn("ZCLOUD_RECEIPT_COVERAGE_AUDIT=", text)
        self.assertIn("ZCLOUD_RECEIPT_COVERAGE_READY=", text)
        self.assertNotIn("urllib.request", text)
        self.assertNotIn("/api/status", text)

    def test_audit_is_strictly_read_only_and_exact_head(self):
        text = self.workflow_text()

        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository", text
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn('f"file:{db_path}?mode=ro"', text)
        self.assertIn("uri=True", text)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', text)
        self.assertIn("before_fingerprint", text)
        self.assertIn("after_fingerprint", text)
        self.assertIn("ZCLOUD_RECEIPT_COVERAGE_DB_IMMUTABLE=1", text)
        self.assertNotIn("project_runtime.init_tables(connection)", text)

        for mutation in (
            "INSERT INTO project_state_receipts",
            "UPDATE project_state_receipts",
            "DELETE FROM project_state_receipts",
            "INSERT INTO portfolio_queue",
            "UPDATE portfolio_queue",
            "DELETE FROM portfolio_queue",
            "portfolio_queue_finish",
        ):
            self.assertNotIn(mutation, text)

    def test_receipt_helpers_work_through_read_only_query_only_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            with sqlite3.connect(db_path) as writable:
                writable.row_factory = sqlite3.Row
                project_runtime.init_tables(writable)
                project_runtime.record_receipt(
                    writable,
                    "cloud",
                    phase="Verifying",
                    ci_status="success",
                    source="test",
                    observed_at="2026-10-06T12:00:00+00:00",
                )
                writable.commit()

            before_bytes = db_path.read_bytes()
            before_stat = db_path.stat()

            readonly = sqlite3.connect(
                f"file:{db_path}?mode=ro",
                uri=True,
                timeout=5,
            )
            readonly.row_factory = sqlite3.Row
            readonly.execute("PRAGMA query_only=ON")
            try:
                latest = project_runtime.latest_receipts(readonly)
                coverage = project_runtime.receipt_coverage(
                    readonly,
                    ["cloud"],
                    max_age_seconds=7200,
                    now_value="2026-10-06T12:30:00+00:00",
                )
            finally:
                readonly.close()

            self.assertEqual(latest["cloud"]["phase"], "Verifying")
            self.assertTrue(coverage["ready"])
            self.assertEqual(coverage["current"], ["cloud"])
            self.assertEqual(db_path.read_bytes(), before_bytes)
            after_stat = db_path.stat()
            self.assertEqual(after_stat.st_size, before_stat.st_size)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
