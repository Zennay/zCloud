import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_repeat_failure_escalation as policy


FP_A = "a" * 64
FP_B = "b" * 64


class RepeatFailureEscalationTests(unittest.TestCase):
    def evaluate(self, rows):
        return policy.evaluate_payload({"attempts": rows})

    def test_first_failed_fix_still_allows_one_more_fix(self):
        result = self.evaluate([{"fingerprint": FP_A, "event": "fix_failed"}])
        self.assertEqual("FIX_ALLOWED", result["decision"])
        self.assertEqual(1, result["consecutive_failed_fixes"])

    def test_second_failed_fix_requires_diagnosis(self):
        result = self.evaluate([
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_failed"},
        ])
        self.assertEqual("DIAGNOSE_REQUIRED", result["decision"])
        self.assertEqual("same_issue_failed_twice", result["reason"])
        self.assertEqual(2, result["consecutive_failed_fixes"])

    def test_third_failed_fix_does_not_reopen_fix_budget(self):
        result = self.evaluate([
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_failed"},
        ])
        self.assertEqual("DIAGNOSE_REQUIRED", result["decision"])
        self.assertEqual(3, result["consecutive_failed_fixes"])

    def test_completed_diagnosis_reopens_one_bounded_fix_attempt(self):
        result = self.evaluate([
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "diagnosed"},
        ])
        self.assertEqual("FIX_ALLOWED", result["decision"])
        self.assertEqual("diagnosis_completed", result["reason"])

    def test_success_resets_issue_attempt_budget(self):
        result = self.evaluate([
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_succeeded"},
        ])
        self.assertEqual("FIX_ALLOWED", result["decision"])
        self.assertEqual(0, result["consecutive_failed_fixes"])

    def test_new_issue_fingerprint_has_separate_budget(self):
        result = self.evaluate([
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_A, "event": "fix_failed"},
            {"fingerprint": FP_B, "event": "fix_failed"},
        ])
        self.assertEqual("FIX_ALLOWED", result["decision"])
        self.assertEqual(1, result["consecutive_failed_fixes"])

    def test_payload_rejects_raw_failure_fields(self):
        with self.assertRaises(policy.EscalationPolicyError):
            self.evaluate([{
                "fingerprint": FP_A,
                "event": "fix_failed",
                "error": "secret raw failure text",
            }])

    def test_invalid_fingerprint_is_rejected(self):
        with self.assertRaises(policy.EscalationPolicyError):
            self.evaluate([{"fingerprint": "not-a-hash", "event": "fix_failed"}])

    def test_history_is_bounded(self):
        with self.assertRaises(policy.EscalationPolicyError):
            self.evaluate([
                {"fingerprint": FP_A, "event": "fix_failed"}
                for _ in range(policy.MAX_HISTORY + 1)
            ])

    def test_cli_require_fix_allowed_fails_when_diagnosis_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempts.json"
            path.write_text(json.dumps({"attempts": [
                {"fingerprint": FP_A, "event": "fix_failed"},
                {"fingerprint": FP_A, "event": "fix_failed"},
            ]}), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/zcloud_repeat_failure_escalation.py",
                    "--input",
                    str(path),
                    "--require-fix-allowed",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(1, proc.returncode)
            output = json.loads(proc.stdout)
            self.assertEqual("DIAGNOSE_REQUIRED", output["decision"])
            self.assertNotIn(FP_A, proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
