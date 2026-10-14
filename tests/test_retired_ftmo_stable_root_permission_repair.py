from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ROOT / ".github/workflows/ftmo-stable-root-permission-repair.yml"


class FtmoStableRootRepairRetirementTests(unittest.TestCase):
    def test_completed_stable_root_repair_lane_stays_retired(self):
        self.assertFalse(
            RETIRED.exists(),
            "one-time FTMO stable-root repair workflow must not regain mutation authority",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
