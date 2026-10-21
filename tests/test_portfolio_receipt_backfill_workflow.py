from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEDICATED_RECEIPT_PROJECTS = {"cloud", "haxlab"}


class PortfolioReceiptBackfillWorkflowTests(unittest.TestCase):
    def test_workflow_is_vps_bounded_and_covers_all_active_projects_without_dedicated_paths(self):
        text = (
            ROOT / ".github" / "workflows" / "project-state-receipt-backfill.yml"
        ).read_text(encoding="utf-8")
        projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        expected = {
            str(project["id"])
            for project in projects
            if str(project.get("status") or "").lower() == "active"
        } - DEDICATED_RECEIPT_PROJECTS

        options_block = text.split("        options:\n", 1)[1].split("\n\npermissions:", 1)[0]
        manual_options = set(re.findall(r"^          - ([a-z0-9-]+)$", options_block, flags=re.MULTILINE))
        matrix_match = re.search(r"\|\| fromJSON\('(\[[^']+\])'\)", text)
        self.assertIsNotNone(matrix_match, "serialized push backfill matrix missing")
        push_matrix = set(json.loads(matrix_match.group(1)))

        self.assertEqual(expected, manual_options)
        self.assertEqual(expected, push_matrix)
        self.assertTrue(DEDICATED_RECEIPT_PROJECTS.isdisjoint(manual_options))
        self.assertTrue(DEDICATED_RECEIPT_PROJECTS.isdisjoint(push_matrix))
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("scripts/zcloud_backfill_project_receipt.py", text)
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
