"""Regression lock for deploy-ops retry/replay review boundary; never authorizes a deploy."""
from pathlib import Path
import unittest

DOC = Path(__file__).resolve().parents[1] / "docs" / "deploy-ops-retry-replay-boundary-20261008.md"


class DeployOpsRetryReplayBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = DOC.read_text(encoding="utf-8")

    def test_retry_identity_bound_to_attempt_and_commits(self):
        for required in ("main SHA", "candidate/head SHA", "run ID", "run attempt", "receipt digest"):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_all_negative_cases_explicitly_stop(self):
        cases = (
            "Same run ID, newer attempt",
            "Same head SHA, different main SHA",
            "Same commit and run, receipt bytes/digest differ",
            "Same receipt, second delivery",
            "One check succeeds, another remains queued/in progress",
            "Check success but missing trusted same-repository workflow provenance",
            "Complete CI but missing production status or post-deploy activation receipt",
            "Serialized production owner #580 or #1089 remains active, unclear, or replaced",
            "Partial production failure followed by a successful retry",
            "Unrelated deploy-ops PR has overlapping paths",
        )
        for case in cases:
            with self.subTest(case=case):
                lines = [line for line in self.content.splitlines() if line.startswith("| " + case + " |")]
                self.assertEqual(1, len(lines))
                self.assertIn("| STOP:", lines[0])

    def test_handoff_flags_never_authorize(self):
        for flag in ("merge_authorized", "deploy_authorized", "mutation_performed"):
            self.assertIn(f'"{flag}": false', self.content)
            self.assertNotIn(f'"{flag}": true', self.content)

    def test_repeated_delivery_not_interpreted_as_second_deployment(self):
        self.assertIn("do not interpret duplicate as a second successful deployment", self.content)

    def test_requires_exact_final_head_validation(self):
        self.assertIn("exact-final-head checks", self.content)
        self.assertIn("old green workflow attempts do not count", self.content)


if __name__ == "__main__":
    unittest.main()
