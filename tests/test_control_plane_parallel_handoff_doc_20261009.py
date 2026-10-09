"""Offline contract checks for the parallel control-plane handoff document.

These tests validate review guidance only. They do not validate runtime locking,
GitHub API behavior, authorization, deployment, or live worker isolation.
"""
from pathlib import Path
import re
import unittest


DOC = Path(__file__).resolve().parents[1] / "docs" / "control-plane-conflict-free-parallel-handoff-20261009.md"


class ParallelHandoffDocumentationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_advisory_not_automatic_authority(self):
        self.assertRegex(self.text, r"(?i)advisory only")
        self.assertRegex(self.text, r"(?i)does not grant merge")
        self.assertRegex(self.text, r"(?i)not an automated lock")

    def test_pr_less_branch_and_semantic_ownership_are_checked(self):
        self.assertIn("PR-less branches", self.text)
        self.assertIn("Compare capability ownership, not just filenames", self.text)
        self.assertIn("new filename does not make overlapping semantics safe", self.text)

    def test_exact_head_terminal_evidence_required(self):
        self.assertIn("final head SHA", self.text)
        self.assertIn("terminal successful checks", self.text)
        self.assertIn("An earlier green run is historical context only", self.text)
        self.assertIn("expected failures", self.text.lower())

    def test_live_gate_and_mutation_boundaries(self):
        for required in (
            "serialized gates",
            "No checklist or green result bypasses an occupied gate",
            "persistent SQLite changes",
            "queue dispatch",
            "service changes",
            "deployment changes",
        ):
            self.assertIn(required.lower(), self.text.lower())

    def test_handoff_record_has_reviewable_identity(self):
        template = self.text.split("## Evidence handoff template", 1)[1]
        for field in (
            "Capability:",
            "Owner / branch / PR:",
            "Base main SHA:",
            "Head SHA:",
            "Changed paths:",
            "Serialized gate snapshot:",
            "Integration reviewer:",
        ):
            self.assertIn(field, template)

    def test_ambiguity_has_explicit_fail_closed_response(self):
        for case in (
            "**Overlapping ownership:**",
            "**Main drift / stale CI:**",
            "**Unknown PR-less branch:**",
            "**Gate occupied:**",
            "**Mismatch between Notion and GitHub:**",
        ):
            self.assertIn(case, self.text)


if __name__ == "__main__":
    unittest.main()
