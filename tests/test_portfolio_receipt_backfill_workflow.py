from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PortfolioReceiptBackfillWorkflowTests(unittest.TestCase):
    def test_workflow_is_vps_bounded_and_bootstraps_only_missing_non_cloud_projects(self):
        text = (
            ROOT / ".github" / "workflows" / "project-state-receipt-backfill.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("scripts/zcloud_backfill_project_receipt.py", text)
        for project_id in ("ftmo", "lightup", "raiseai", "supa", "ulab", "zssh"):
            self.assertIn(project_id, text)
        self.assertNotIn('["cloud"', text)
        self.assertNotIn('["haxlab"', text)
        self.assertIn("max-parallel: 1", text)
        self.assertIn("fail-fast: false", text)

    def test_backfill_script_never_mutates_portfolio_queue(self):
        text = (ROOT / "scripts" / "zcloud_backfill_project_receipt.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SELECT * FROM portfolio_queue WHERE project_id=?", text)
        self.assertNotIn("UPDATE portfolio_queue", text)
        self.assertNotIn("INSERT INTO portfolio_queue", text)
        self.assertNotIn("DELETE FROM portfolio_queue", text)
        self.assertIn("PORTFOLIO_STATE_RECEIPT_READBACK_GREEN", text)


if __name__ == "__main__":
    unittest.main()
