from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HaxLabReceiptBackfillWorkflowTests(unittest.TestCase):
    def test_backfill_is_bounded_to_receipt_write_on_permanent_runner(self):
        text = (ROOT / ".github/workflows/haxlab-state-receipt-backfill.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('Path("/home/ubuntu/zennay-cloud")', text)
        self.assertIn('"projects.json"', text)
        self.assertIn('"history.db"', text)
        self.assertIn("PRAGMA table_info(portfolio_queue)", text)
        self.assertIn("SELECT * FROM portfolio_queue WHERE project_id=?", text)
        self.assertIn('"receipt"', text)
        self.assertIn('"haxlab"', text)
        self.assertIn('"github-actions:haxlab-state-receipt-backfill"', text)
        self.assertIn("HAXLAB_STATE_RECEIPT_BACKFILL_GREEN", text)
        self.assertIn("HAXLAB_STATE_RECEIPT_READBACK_GREEN", text)
        self.assertIn("SELECT id,ci_status,source FROM project_state_receipts", text)
        self.assertIn("HaxLab receipt readback missing after write", text)
        self.assertNotIn('action":"result"', text)
        self.assertNotIn("portfolio_queue_finish", text)

    def test_backfill_fails_closed_without_live_project_and_queue_evidence(self):
        text = (ROOT / ".github/workflows/haxlab-state-receipt-backfill.yml").read_text(encoding="utf-8")
        self.assertIn("haxlab project missing from runtime-owned projects.json", text)
        self.assertIn("no HaxLab portfolio queue evidence available", text)
        self.assertIn("latest HaxLab queue state is not admissible evidence", text)
        self.assertIn("live HaxLab phase/next_step is incomplete", text)


if __name__ == "__main__":
    unittest.main()
