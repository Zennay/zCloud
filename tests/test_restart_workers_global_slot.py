import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersGlobalSlotTests(unittest.TestCase):
    def test_restart_uses_canonical_global_slot_field(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('row.get("global_worker_slot")', text)
        self.assertNotIn('int(row["slot"])', text)
        self.assertIn("Allocated worker missing global_worker_slot", text)


if __name__ == "__main__":
    unittest.main()
