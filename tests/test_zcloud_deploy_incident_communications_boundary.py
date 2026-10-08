"""Static guard for the non-authorizing deploy-ops incident communications template."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "deploy-ops-incident-communications-boundary.md"


class DeployOpsCommunicationsBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = DOC.read_text(encoding="utf-8")

    def test_verification_and_correction_controls(self):
        for required in (
            "independently verifies external production health",
            "timestamped corrections",
            "communications owner",
            "redaction",
        ):
            with self.subTest(required=required):
                self.assertIn(required.lower(), self.content.lower())

    def test_no_operational_authorization(self):
        for name in (
            "release_authorized", "merge_authorized", "deploy_authorized",
            "rollback_authorized", "mutation_performed",
        ):
            with self.subTest(name=name):
                self.assertIn(f"`{name}=false`", self.content)
                self.assertNotIn(f"`{name}=true`", self.content)

    def test_unknown_evidence_fails_closed(self):
        for required in (
            "leave the incident status as **investigating**",
            "do not speculate",
            "not approval for deployment",
        ):
            with self.subTest(required=required):
                self.assertIn(required.lower(), self.content.lower())


if __name__ == "__main__":
    unittest.main()
