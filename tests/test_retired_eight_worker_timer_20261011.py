"""Archived 2026-10-09 overnight burst must not recur indefinitely."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / ".github" / "workflows" / "zcloud-overnight-eight-20261009.yml"


class RetiredEightWorkerBurstTests(unittest.TestCase):
    def test_dated_incident_never_auto_reactivates(self):
        source = PATH.read_text(encoding="utf-8")
        header = source.split("\npermissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", header)
        self.assertNotIn("  schedule:", header)
        self.assertNotIn("  push:", header)
        self.assertNotIn("  workflow_run:", header)
        self.assertNotIn("    - cron:", header)

    def test_preserves_explicit_owner_memory_preflight(self):
        source = PATH.read_text(encoding="utf-8")
        self.assertIn("memory", source)
        self.assertIn("SwapFree", source)
        self.assertIn("chatgpt_count", source)


if __name__ == "__main__":
    unittest.main()
