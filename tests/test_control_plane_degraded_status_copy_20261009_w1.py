"""Offline contract checks for control-plane degraded status copy. No external services."""
from pathlib import Path
import unittest

DOC = Path(__file__).resolve().parents[1] / "docs" / "control-plane-degraded-status-copy-20261009-w1.md"

class DegradedStatusCopyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_each_ambiguous_state_has_non_authorizing_label(self):
        for label in (
            "Prompt submitted", "Status unknown", "Service temporarily unavailable",
            "Claim state unverified", "Checks pending for current revision",
            "API responding", "Conflicting observations",
        ):
            with self.subTest(label=label):
                self.assertIn("| " + label + " |", self.text)

    def test_misleading_health_claims_are_forbidden(self):
        for bad_claim in (
            "Working now", "Offline / crashed", "Workers stopped",
            "Worker released", "Ready to deploy", "Workers generating",
        ):
            with self.subTest(claim=bad_claim):
                self.assertIn("| " + bad_claim + " |", self.text)

    def test_no_action_authority(self):
        for key in ("mutation_authorized", "restart_authorized", "merge_authorized", "deploy_authorized"):
            self.assertIn("`" + key + "=false`", self.text)

    def test_provenance_and_accessibility_are_mandatory(self):
        for term in ("screen-reader", "Timestamps include timezone", "exact PR SHA", "source and scope"):
            self.assertIn(term, self.text)

if __name__ == "__main__":
    unittest.main()
