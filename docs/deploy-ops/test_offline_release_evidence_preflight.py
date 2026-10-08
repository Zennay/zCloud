#!/usr/bin/env python3
"""Standard-library tests for the offline evidence-only deploy-ops preflight."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).with_name("offline_release_evidence_preflight.py")
spec = importlib.util.spec_from_file_location("offline_preflight", MODULE)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

BASE = {
    "repository": "Zennay/zCloud",
    "main_sha": "a" * 40,
    "run_head_sha": "a" * 40,
    "workflow_run_id": 12345,
    "run_status": "completed",
    "run_conclusion": "success",
    "run_event": "push",
    "expected_event": "push",
    "trusted_origin": True,
    "owner_confirmed": True,
    "environment": "production",
}

class EvidencePreflightTests(unittest.TestCase):
    def test_consistent_data_is_only_consistent(self):
        self.assertEqual(preflight.validate(dict(BASE)), [])

    def test_missing_fields_fail_closed(self):
        for key in BASE:
            with self.subTest(key=key):
                candidate = dict(BASE)
                candidate.pop(key)
                self.assertTrue(preflight.validate(candidate))

    def test_untrusted_or_stale(self):
        for key, value in [
            ("repository", "Zennay/other"),
            ("main_sha", "b" * 40),
            ("run_head_sha", "b" * 40),
            ("run_status", "queued"),
            ("run_conclusion", "cancelled"),
            ("run_event", "pull_request"),
            ("trusted_origin", False),
            ("owner_confirmed", False),
            ("environment", "staging"),
            ("workflow_run_id", True),
            ("workflow_run_id", -1),
            ("workflow_run_id", "12"),
        ]:
            with self.subTest(key=key, value=value):
                candidate = dict(BASE)
                candidate[key] = value
                self.assertTrue(preflight.validate(candidate))

    def test_malformed_inputs_do_not_pass(self):
        for candidate in (None, [], "success", {}, {"main_sha": None}):
            with self.subTest(candidate=candidate):
                self.assertTrue(preflight.validate(candidate))

if __name__ == "__main__":
    unittest.main()
