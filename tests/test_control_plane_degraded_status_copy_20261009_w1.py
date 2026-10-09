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


    def test_table_has_unique_evidence_and_expected_denial_pairs(self):
        rows = [line for line in self.text.splitlines() if line.startswith("| ")]
        records = [[cell.strip() for cell in line.strip("|").split("|")] for line in rows]
        records = [cells for cells in records if len(cells) == 4 and cells[0] not in ("Available evidence", "---")]
        self.assertEqual(8, len(records))
        self.assertEqual(len(records), len({cells[0] for cells in records}))
        expectations = {
            "Prompt-sent only": ("Prompt submitted", "Working now"),
            "Stale heartbeat": ("Status unknown", "Offline / crashed"),
            "API 503 or timeout": ("Service temporarily unavailable", "Workers stopped"),
            "Queue claim expired or missing from GET": ("Claim state unverified", "Worker released"),
            "GitHub PR checks on previous commit": ("Checks pending for current revision", "Ready to deploy"),
            "Fresh healthy endpoint only": ("API responding", "Workers generating"),
            "Conflicting clocks or producers": ("Conflicting observations", "Healthy"),
            "Fresh, correlated generation-start with worker identity": ("Generation observed", "Task completed"),
        }
        actual = {cells[0]: (cells[1], cells[3]) for cells in records}
        self.assertEqual(expectations, actual)

    def test_no_privileged_action_is_implied_by_display(self):
        self.assertIn("never grants permission to dispatch, restart, merge, deploy or modify queues", self.text)
        self.assertIn("does not imply material progress", self.text)


    def test_multi_source_precedence_fails_closed(self):
        for phrase in (
            "No evidence / unavailable producer",
            "Contradictory fresh producers",
            "One fresh source, one stale source",
            "Replayed event or missing worker identity",
            "Clock skew, missing timezone or future timestamps",
            "Recovered telemetry",
            "Unverified recovery action",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)
        self.assertIn("do not pick the most optimistic state", self.text)
        self.assertIn("cannot authorize recovery actions", self.text)

if __name__ == "__main__":
    unittest.main()
