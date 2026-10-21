"""Offline redaction safety regression; no production workflow integration."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "deploy_evidence_redaction_offline_20261008.py"
spec = importlib.util.spec_from_file_location("deploy_evidence_redaction_offline", MODULE)
redaction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redaction)


class RedactionTests(unittest.TestCase):
    def test_authorization_line_is_not_echoed(self):
        secret = "Bearer sample-private-test-value"
        output = redaction.redact_evidence("Authorization: " + secret + "\nstatus: safe")
        self.assertNotIn(secret, output)
        self.assertIn("status: safe", output)

    def test_assignment_variants_fail_closed(self):
        for key in ("GITHUB_TOKEN", "api_key", "CLIENT_SECRET", "password", "refresh_token"):
            with self.subTest(key=key):
                self.assertEqual(redaction.redact_evidence(f"{key}=sensitive"), "[REDACTED CREDENTIAL LINE]")

    def test_embedded_github_token(self):
        secret = "ghp_" + "a" * 20
        self.assertNotIn(secret, redaction.redact_evidence("failed: " + secret))

    def test_bearer_in_message(self):
        self.assertNotIn("private-jwt", redaction.redact_evidence("auth Bearer private-jwt failed"))

    def test_preserves_benign_status(self):
        self.assertEqual(redaction.redact_evidence("deploy denied\nsha 123abc"), "deploy denied\nsha 123abc")

    def test_bound_and_truncation(self):
        out = redaction.redact_evidence("A" * 200, max_chars=50)
        self.assertTrue(out.startswith("A" * 50))
        self.assertTrue(out.endswith("[TRUNCATED]"))

    def test_invalid_parameters(self):
        for value in (None, 4, b"abc"):
            with self.assertRaises(ValueError):
                redaction.redact_evidence(value)
        for limit in (0, 12001):
            with self.assertRaises(ValueError):
                redaction.redact_evidence("ok", max_chars=limit)


if __name__ == "__main__":
    unittest.main()
