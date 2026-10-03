from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HaxLabReceiptWorkflowTests(unittest.TestCase):
    def test_arena_v2_queue_advance_records_evidence_backed_receipt(self):
        text = (ROOT / ".github/workflows/haxlab-arena-v2-queue-advance.yml").read_text(encoding="utf-8")
        self.assertIn("uses: actions/checkout@v4", text)
        self.assertIn("Record evidence-backed HaxLab Arena v2 state receipt", text)
        self.assertIn("--project haxlab", text)
        self.assertIn("--ci-status success", text)
        self.assertIn("88568e6e28610d60735c6e7089997003c7e25a78", text)
        self.assertIn('"evidence_marker": "HAXLAB_ARENA_V2_QUEUE_ADVANCE_GREEN"', text)
        self.assertIn('--source "github-actions:haxlab-arena-v2-queue-advance"', text)
        queue_pos = text.index("Close Arena v2 P1 and claim next HaxLab write lane")
        receipt_pos = text.index("Record evidence-backed HaxLab Arena v2 state receipt")
        self.assertLess(queue_pos, receipt_pos)


if __name__ == "__main__":
    unittest.main()
