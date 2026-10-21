from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RetiredFtmoPr496Pr518WorkflowTests(unittest.TestCase):
    def test_terminal_ftmo_pr496_pr518_entrypoints_stay_retired(self):
        retired = [
            WORKFLOWS / "ftmo-pr496-research-state-sync-proof.yml",
            WORKFLOWS / "ftmo-pr518-provider-bundle-proof.yml",
        ]
        for path in retired:
            self.assertFalse(
                path.exists(),
                f"retired terminal FTMO workflow must not return: {path.name}",
            )

    def test_terminal_heads_are_not_reintroduced_in_active_workflows(self):
        terminal_heads = {
            "9f2a986c8462b15a3353ddce44435417acc591e0",
            "22c9b6d8596630b929623d64120d5d695da9942e",
        }
        offenders = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            matched = sorted(head for head in terminal_heads if head in text)
            if matched:
                offenders[path.name] = matched
        self.assertEqual(
            offenders,
            {},
            f"terminal FTMO PR heads must not be targeted by active workflows: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
