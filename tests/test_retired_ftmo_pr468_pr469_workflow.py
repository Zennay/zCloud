from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RetiredFtmoPr468Pr469WorkflowTests(unittest.TestCase):
    def test_terminal_pr468_pr469_combined_proof_stays_retired(self):
        retired = WORKFLOWS / "ftmo-pr468-469-exact-head-proof.yml"
        self.assertFalse(
            retired.exists(),
            f"retired terminal FTMO proof must not return: {retired.name}",
        )

    def test_pr468_terminal_head_is_not_reintroduced_in_active_workflows(self):
        terminal_head = "0ff8fd49547a141dc87d5c4a933be758a4a1d6c8"
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if terminal_head in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            f"merged FTMO PR468 head must not be targeted by active workflows: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
