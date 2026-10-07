from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-state-receipt-coverage-audit.yml"


class StateReceiptCoverageAuditWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_without_reading_live_state(self) -> None:
        text = self.text
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn(
            "python3 -m unittest tests/test_state_receipt_coverage_audit_workflow.py",
            text,
        )

        proof = text[text.index("\n  prove:\n") : text.index("\n  audit:\n")]
        self.assertIn("needs: validate", proof)
        self.assertIn("github.actor == 'Zennay'", proof)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", proof)
        self.assertIn("ZCLOUD_EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", proof)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', proof)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json >/dev/null", proof)
        self.assertNotIn("/home/ubuntu/zennay-cloud/history.db", proof)
        self.assertNotIn("project_runtime.receipt_coverage(", proof)

    def test_live_audit_is_manual_main_only_exact_revision_and_permanent_vps_bound(self) -> None:
        live = self.text[self.text.index("\n  audit:\n") :]
        self.assertIn("github.event_name == 'workflow_dispatch'", live)
        self.assertIn("github.repository == 'Zennay/zCloud'", live)
        self.assertIn("github.ref == 'refs/heads/main'", live)
        self.assertIn("github.actor == 'Zennay'", live)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", live)
        self.assertIn("ZCLOUD_EXPECTED_SHA: ${{ github.sha }}", live)
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", live)
        self.assertIn("persist-credentials: false", live)
        self.assertIn("clean: true", live)
        self.assertIn("fetch-depth: 1", live)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', live)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json >/dev/null", live)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', live)
        self.assertIn('test "$(id -un)" = "ubuntu"', live)
        self.assertIn('test "$(id -u)" -ne 0', live)

    def test_audit_reads_vps_source_of_truth_through_strict_read_only_sqlite(self) -> None:
        text = self.text
        self.assertIn('Path("/home/ubuntu/zennay-cloud")', text)
        self.assertIn('"projects.json"', text)
        self.assertIn('"history.db"', text)
        self.assertIn("project_runtime.receipt_coverage(", text)
        self.assertIn("project_runtime.latest_receipts(", text)
        self.assertIn("max_age_seconds=7200", text)
        self.assertIn('?mode=ro"', text)
        self.assertIn("sqlite3.connect(db_uri, uri=True", text)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', text)
        self.assertIn("before = db_path.stat()", text)
        self.assertIn("after = db_path.stat()", text)
        self.assertIn("history.db changed during read-only coverage audit", text)
        self.assertIn("db_path.is_symlink()", text)
        self.assertNotIn("project_runtime.init_tables(", text)
        self.assertNotIn("urllib.request", text)
        self.assertNotIn("/api/status", text)

    def test_audit_output_is_bounded_and_runtime_mutation_is_absent(self) -> None:
        text = self.text
        self.assertIn("ZCLOUD_RECEIPT_COVERAGE_AUDIT=", text)
        self.assertIn("ZCLOUD_RECEIPT_COVERAGE_READY=", text)
        self.assertIn('"db_immutable": True', text)
        self.assertIn('"ci_status": receipt.get("ci_status")', text)
        self.assertIn('"observed_at": receipt.get("observed_at")', text)
        self.assertNotIn('"next_gate": receipt.get("next_gate")', text)
        self.assertNotIn('"phase": receipt.get("phase")', text)
        self.assertNotIn('"source": receipt.get("source")', text)

        for mutation in (
            "INSERT INTO project_state_receipts",
            "UPDATE project_state_receipts",
            "DELETE FROM project_state_receipts",
            "INSERT INTO portfolio_queue",
            "UPDATE portfolio_queue",
            "DELETE FROM portfolio_queue",
            "portfolio_queue_finish",
            "systemctl restart",
            "systemctl start",
            "systemctl stop",
        ):
            self.assertNotIn(mutation, text)

    def test_remote_actions_are_immutable_and_pr_cancellation_cannot_cancel_live(self) -> None:
        text = self.text
        action_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("uses:")
        ]
        self.assertGreaterEqual(len(action_lines), 3)
        for line in action_lines:
            self.assertRegex(line, r"^uses: [^@]+@[0-9a-f]{40}(?: # .+)?$")
        self.assertIn("format('pr-{0}', github.event.pull_request.number)", text)
        self.assertIn("|| 'live'", text)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
