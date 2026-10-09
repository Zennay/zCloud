"""Offline regression for the operator-only zCloud incident triage contract.

Run: python3 -m unittest tests/test_control_plane_incident_triage_contract.py
No services, external API requests or environment tokens are needed.
"""
from pathlib import Path
import unittest

DOC = Path(__file__).resolve().parents[1] / "docs" / "control-plane-incident-triage.md"

REQUIRED = (
    "observed_at_utc:",
    "main_sha:",
    "deployed_sha:",
    "run_id_attempt_environment:",
    "current_owner_and_gate:",
    "mutation_authorized: false",
    "redactions_applied: yes",
    "Ownership collision",
    "Evidence invalid",
    "Read-only triage order",
)


def validate(text: str) -> list[str]:
    """Return contract failures without opening any runtime resource."""
    failures = [f"missing: {field}" for field in REQUIRED if field not in text]
    if "does **not** authorize" not in text:
        failures.append("must expressly deny implicit authorization")
    if "Rollback is itself a write" not in text:
        failures.append("rollback boundary absent")
    if "SHA + ID + attempt + environment" not in text:
        failures.append("proof identity dimensions absent")
    exact_fields = [line.strip() for line in text.splitlines()]
    if exact_fields.count("mutation_authorized: false") != 1:
        failures.append("expected exactly one explicit non-authorizing handoff")
    if any(line.startswith("mutation_authorized:") and line != "mutation_authorized: false" for line in exact_fields):
        failures.append("contradictory mutation authorization")
    if any(line.startswith("redactions_applied:") and line != "redactions_applied: yes" for line in exact_fields):
        failures.append("redactions may not be disabled")
    return failures


class IncidentTriageContract(unittest.TestCase):
    def test_checked_in_document(self) -> None:
        self.assertEqual(validate(DOC.read_text(encoding="utf-8")), [])

    def test_mandatory_evidence_failure(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        for field in REQUIRED:
            with self.subTest(field=field):
                self.assertTrue(validate(doc.replace(field, "REMOVED")), field)

    def test_authority_grant_is_never_a_valid_template(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertTrue(validate(doc.replace("mutation_authorized: false", "mutation_authorized: true")))

    def test_comment_does_not_satisfy_authority_field(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertTrue(validate(doc.replace(
            "mutation_authorized: false",
            "# mutation_authorized: false",
        )))

    def test_contradictory_authorization_must_fail(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertTrue(validate(doc + "\nmutation_authorized: true\n"))

    def test_redaction_override_must_fail(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertTrue(validate(doc.replace(
            "redactions_applied: yes", "redactions_applied: no",
        )))

    def test_rollback_disclaimer_must_remain(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertTrue(validate(doc.replace("Rollback is itself a write", "Rollback")))


if __name__ == "__main__":
    unittest.main()
