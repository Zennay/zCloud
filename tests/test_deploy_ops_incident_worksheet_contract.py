"""Static fail-closed contract for the deploy-ops incident evidence worksheet.

This verifies documentation shape only. It does not authenticate incident evidence,
authorize release actions, access the network, or modify production.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKSHEET = ROOT / "docs" / "deploy-ops-incident-evidence-capture-worksheet.md"


class IncidentWorksheetContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = WORKSHEET.read_text(encoding="utf-8")

    def test_identifies_exact_commits_and_time(self):
        for marker in (
            "UTC, ISO-8601",
            "40-character commit SHA",
            "40-character head SHA",
            "Observed deployment SHA",
            "unknown",
            "Captured UTC",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_provenance_and_independent_health_are_explicit(self):
        for marker in (
            "Exact-head regression",
            "Permanent VPS runner identity",
            "Serialized owner inventory",
            "Production deployment workflow",
            "Independent external health check",
            "Post-deploy receipt",
            "limitations",
            "Evidence invalidated",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker.lower(), self.doc.lower())

    def test_all_authorization_flags_default_false(self):
        for flag in (
            "release_authorized",
            "merge_authorized",
            "deploy_authorized",
            "rollback_authorized",
            "mutation_performed",
        ):
            with self.subTest(flag=flag):
                self.assertIn(f"`{flag}=false`", self.doc)
                self.assertNotIn(f"`{flag}=true`", self.doc)

    def test_escalation_and_no_implicit_authority(self):
        for marker in (
            "no authorization is inferred",
            "explicit independent approval",
            "no permission to restart",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)


if __name__ == "__main__":
    unittest.main()
