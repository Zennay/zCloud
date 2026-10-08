"""Negative proof for the offline deployment evidence publisher."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from zcloud_offline_deploy_evidence_allowlist import sanitize


class EvidenceAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.good = {"schema_version": 1, "check": "postdeploy_receipt",
                     "status": "pass", "reason_code": "NONE",
                     "candidate_sha": "a" * 40, "deployed_sha": "a" * 40,
                     "run_id": 123456, "changed_field_count": 0}

    def test_allowed_data_is_bounded_and_non_authorizing(self):
        result = sanitize(self.good)
        self.assertEqual(result["candidate_sha"], "a" * 40)
        for field in ("release_authorized", "merge_authorized", "deploy_authorized", "mutation_performed"):
            self.assertIs(result[field], False)

    def test_reject_unknown_private_fields(self):
        for field in ("api_token", "environment", "raw_response", "debug", "private_url", "local_path"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    sanitize(dict(self.good, **{field: "SECRET_DO_NOT_PUBLISH"}))

    def test_reject_invalid_identifiers(self):
        for key, val in (("candidate_sha", "b" * 39), ("deployed_sha", "B" * 40),
                         ("run_id", True), ("run_id", -1), ("changed_field_count", 10001),
                         ("changed_field_count", False), ("schema_version", True)):
            with self.subTest(key=key, val=val):
                with self.assertRaises(ValueError):
                    sanitize(dict(self.good, **{key: val}))

    def test_cli_failure_never_echoes_sensitive_input(self):
        sensitive = {"schema_version": 1, "check": "postdeploy_receipt",
                     "status": "pass", "reason_code": "NONE",
                     "secret": "SENTINEL_PRIVATE_TOKEN_OR_PATH"}
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "zcloud_offline_deploy_evidence_allowlist.py")],
                                input=json.dumps(sensitive), text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "fail")
        self.assertIs(output["deploy_authorized"], False)

    def test_invalid_json_is_bounded(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "zcloud_offline_deploy_evidence_allowlist.py")],
                                input='{"api_key":"SECRET"', text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SECRET", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
