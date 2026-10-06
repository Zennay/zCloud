from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RetiredFtmoPr448Pr462WorkflowTests(unittest.TestCase):
    def test_terminal_ftmo_pr448_pr462_entrypoints_stay_retired(self):
        retired = [
            WORKFLOWS / "ftmo-pr448-live-finalization-snapshot.yml",
            WORKFLOWS / "ftmo-pr462-monotonic-sync-proof.yml",
        ]
        for path in retired:
            self.assertFalse(
                path.exists(),
                f"retired terminal FTMO workflow must not return: {path.name}",
            )

    def test_pr462_terminal_head_is_not_reintroduced_in_active_workflows(self):
        terminal_head = "9e488119a50f55f21e4bee431354ba8cb9e9196c"
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if terminal_head in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            f"closed-unmerged FTMO PR462 head must not be targeted by active workflows: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
