"""Read-only dashboard job-result triage regression."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "triage", Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_check_result_triage.py"
)
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)

class TriageTests(unittest.TestCase):
    def test_recovery_failure_is_not_dashboard_failure(self):
        x = triage.classify({"jobs": [{"name": "recover", "conclusion": "failure"},
                                      {"name": "external_verify", "conclusion": "success"}]})
        self.assertEqual(x["status"], "recovery_failed_external_healthy")
        self.assertFalse(x["deploy_authorized"])
        self.assertFalse(x["recovery_authorized"])
        self.assertFalse(x["mutation_performed"])

    def test_missing_evidence_fails_closed(self):
        self.assertEqual(triage.classify({"jobs": []})["status"], "incomplete_evidence")

    def test_no_duplicate_trusted_job(self):
        with self.assertRaises(ValueError):
            triage.classify({"jobs": [{"name": "recover", "conclusion": "success"},
                                      {"name": "recover", "conclusion": "failure"}]})

    def test_outage_not_inferred_from_external_check(self):
        x = triage.classify({"jobs": [{"name": "recover", "conclusion": "success"},
                                      {"name": "external_verify", "conclusion": "failure"}]})
        self.assertEqual(x["status"], "external_check_failed_not_proof_of_outage")

    def test_missing_conclusion_rejected(self):
        with self.assertRaises(ValueError):
            triage.classify({"jobs": [{"name": "recover", "conclusion": None}]})

    def test_non_string_conclusions_rejected(self):
        for value in ([], {}, 0, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    triage.classify({"jobs": [{"name": "recover", "conclusion": value}]})

    def test_both_successes_do_not_authorize_repair(self):
        x = triage.classify({"jobs": [{"name": "recover", "conclusion": "success"},
                                      {"name": "external_verify", "conclusion": "success"}]})
        self.assertEqual(x["status"], "both_checks_succeeded_not_deploy_authority")
        self.assertFalse(x["recovery_authorized"])
        self.assertFalse(x["deploy_authorized"])

if __name__ == "__main__":
    unittest.main()
