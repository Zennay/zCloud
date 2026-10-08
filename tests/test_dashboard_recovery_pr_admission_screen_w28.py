"""Offline regression tests; no dispatch, runner or network access."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dashboard_recovery_pr_admission_screen_w28 import recovery_pr_risk

HEADER = "on:\n  pull_request:\n    branches: [main]\njobs:\n"
BODY = "  recover:\n    runs-on: self-hosted\n    steps:\n      - run: sudo systemctl restart zennay-cloud\n"
QUARANTINED = "  recover:\n    if: ${{ false }}\n    runs-on: self-hosted\n    steps:\n      - run: sudo systemctl restart zennay-cloud\n"


class AdmissionScreenTests(unittest.TestCase):
    def test_unquarantined_privileged_job_is_exposed(self):
        self.assertEqual(recovery_pr_risk(HEADER + BODY), "exposed")

    def test_explicit_job_level_false_is_quarantined(self):
        self.assertEqual(recovery_pr_risk(HEADER + QUARANTINED), "quarantined")

    def test_step_level_false_is_not_job_quarantine(self):
        self.assertEqual(recovery_pr_risk(HEADER + BODY.replace("    steps:", "    steps:\n      - if: ${{ false }}")), "exposed")

    def test_comment_is_not_job_quarantine(self):
        self.assertEqual(recovery_pr_risk(HEADER + BODY.replace("    runs-on:", "    # if: ${{ false }}\n    runs-on:")), "exposed")

    def test_nonliteral_condition_is_not_quarantine(self):
        self.assertEqual(recovery_pr_risk(HEADER + BODY.replace("    runs-on:", "    if: ${{ github.event_name != 'pull_request' }}\n    runs-on:")), "exposed")

    def test_missing_event_needs_review(self):
        self.assertEqual(recovery_pr_risk(BODY), "review")

    def test_missing_job_needs_review(self):
        self.assertEqual(recovery_pr_risk(HEADER), "review")

    def test_unknown_runner_shape_needs_review(self):
        self.assertEqual(recovery_pr_risk(HEADER + BODY.replace("self-hosted", "[self-hosted, linux]")), "review")

    def test_nontext_needs_review(self):
        self.assertEqual(recovery_pr_risk(None), "review")


if __name__ == "__main__":
    unittest.main()
