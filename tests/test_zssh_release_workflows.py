from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ZsshReleaseWorkflowTests(unittest.TestCase):
    def test_public_plugin_queue_stays_below_ftmo_p0(self):
        text = (ROOT / ".github/workflows/zssh-public-plugin-priority.yml").read_text(encoding="utf-8")
        self.assertIn('"priority": "P1"', text)
        self.assertIn('!= "P1"', text)
        self.assertNotIn('"priority": "P0"', text)

    def test_vps_release_uses_permanent_runner_guard(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        match = re.search(r"ZSSH_RELEASE_SHA:\s*([0-9a-f]{40})", text)
        self.assertIsNotNone(match)


if __name__ == "__main__":
    unittest.main()
