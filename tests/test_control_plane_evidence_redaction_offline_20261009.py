"""Isolated reference tests: no production integration."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "offline_redaction",
    ROOT / "scripts/control_plane_evidence_redaction_offline_20261009.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class EvidenceRedactionTests(unittest.TestCase):
    def test_sensitive_fields_recursive(self):
        result = module.classify_handoff({
            "worker": "worker-1",
            "nested": {"access_token": "example-secret", "cookie": "session"},
            "records": [{"API-KEY": "hidden", "state": "running"}],
        })
        self.assertEqual(result["evidence"]["nested"]["access_token"], "[REDACTED]")
        self.assertEqual(result["evidence"]["nested"]["cookie"], "[REDACTED]")
        self.assertEqual(result["evidence"]["records"][0]["API-KEY"], "[REDACTED]")
        self.assertEqual(result["evidence"]["worker"], "worker-1")

    def test_inline_credentials_are_masked(self):
        value = "Authorization: Bearer abc.def.ghi / ghp_abcdefghijklmnopqrstuvwxyz"
        sanitized = module.sanitize(value)
        self.assertNotIn("abc.def.ghi", sanitized)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", sanitized)

    def test_unknown_types_fail_closed(self):
        class Hostile:
            def __str__(self):
                raise AssertionError("must not stringify")
        self.assertEqual(module.sanitize(Hostile()), "[REDACTED]")

    def test_depth_and_size_limits(self):
        nested = {"item": "x"}
        for _ in range(20):
            nested = {"next": nested}
        self.assertIn("[TRUNCATED]", str(module.sanitize(nested)))
        self.assertEqual(len(module.sanitize(list(range(300)))), 201)
        self.assertTrue(module.sanitize("x" * 5000).endswith("[TRUNCATED]"))

    def test_never_authorizes_mutation(self):
        for evidence in ({"status": "success"}, None, {"token": "abc"}):
            result = module.classify_handoff(evidence)
            for flag in ("authenticated_origin", "authorizes_restart",
                         "authorizes_queue_write", "authorizes_deploy",
                         "mutation_performed"):
                self.assertIs(result[flag], False)


if __name__ == "__main__":
    unittest.main()
