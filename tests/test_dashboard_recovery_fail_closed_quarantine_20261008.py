"""Regression contract for the temporary privileged dashboard recovery quarantine.

This is a static guard, not a substitute for the eventual trusted recovery gate.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-dashboard-access-recovery.yml"


class DashboardRecoveryQuarantineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_recovery_job_is_unconditionally_disabled(self):
        section = self.source.split("\n  recover:\n", 1)[1].split("\n  external_verify:\n", 1)[0]
        self.assertRegex(section, r"(?m)^    if: $\\{\\{ false }}$")
        self.assertIn("runs-on: self-hosted", section)

    def test_external_verification_remains_independent_and_hosted(self):
        section = self.source.split("\n  external_verify:\n", 1)[1]
        self.assertIn("runs-on: ubuntu-latest", section)
        self.assertIn("ZCLOUD_DASHBOARD_EXTERNAL_STATUS_GREEN", section)
        self.assertNotRegex(section, r"(?m)^    needs:.*recover")

    def test_pr_checks_still_exist(self):
        self.assertRegex(self.source, r"(?m)^  pull_request:")
        self.assertRegex(self.source, r"(?m)^    branches: [main]$")


if __name__ == "__main__":
    unittest.main()
