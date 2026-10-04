from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StateReceiptCoverageAuditWorkflowTests(unittest.TestCase):
    def test_audit_reads_vps_source_of_truth_without_dashboard_http_dependency(self):
        text = (
            ROOT / ".github" / "workflows" / "zcloud-state-receipt-coverage-audit.yml"
        ).read_text(encoding="utf-8")

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

    def test_audit_is_read_only_for_runtime_queue_and_receipts(self):
        text = (
            ROOT / ".github" / "workflows" / "zcloud-state-receipt-coverage-audit.yml"
        ).read_text(encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
