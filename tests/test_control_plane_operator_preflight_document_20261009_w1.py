"""Offline contract checks for the operator-preflight denial documentation.

Does not import or call runtime, network, SQLite or deployment code.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "control-plane-operator-preflight-denial-20261009-w1.md"


class OperatorPreflightDocumentContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_all_seven_decision_evidence_fields_present(self):
        for field in (
            "Proposed operation", "Current owner", "Canonical state",
            "Positive authority", "Safety guards", "Rollback", "Outcome",
        ):
            with self.subTest(field=field):
                self.assertRegex(self.text, rf"(?m)^\| {re.escape(field)} \|")

    def test_ten_numbered_deny_conditions(self):
        items = re.findall(r"(?m)^([0-9]+)\. ", self.text)
        self.assertEqual(items, [str(i) for i in range(1, 11)])

    def test_tabletop_has_seven_explicit_dispositions(self):
        section = self.text.split("## Acceptance exercises (tabletop only)", 1)[1].split("## Handoff", 1)[0]
        scenarios = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| Scenario |") and not line.startswith("| ---")]
        self.assertEqual(len(scenarios), 7)
        for line in scenarios:
            self.assertTrue(any(token in line for token in ("DENY", "Eligibility can be recorded")))

    def test_no_runtime_authority_claim(self):
        for statement in (
            "not deployed or wired to runtime",
            "does *not* grant privileges",
            "does not authenticate any observations",
            "does not execute anything",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement.casefold(), self.text.casefold())


if __name__ == "__main__":
    unittest.main()
