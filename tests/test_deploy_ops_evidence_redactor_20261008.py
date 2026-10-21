"""Contract tests for standalone redacted deploy-ops evidence output."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy_ops_evidence_redactor_20261008.py"
spec = importlib.util.spec_from_file_location("deploy_evidence_redactor", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SHA = "a" * 40

def fixture():
    return {
        "repository": "Zennay/zCloud", "candidate_sha": SHA, "main_sha": SHA,
        "regression": {"status": "success", "token": "secret-regression"},
        "vps_probe": {"status": "success", "ssh_private_key": "secret-private"},
        "production_receipt": {"status": "unknown", "logs": "secret-journal"},
        "github_token": "secret-top-level",
    }

class EvidenceRedactionTests(unittest.TestCase):
    def test_allowlist_strips_nested_and_top_level_secrets(self):
        result = module.redact(fixture())
        dumped = json.dumps(result)
        self.assertNotIn("secret-", dumped)
        self.assertEqual(result["vps_probe"], {"status": "success"})
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["recovery_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_rejects_missing_or_unsafe_identity(self):
        for key, val in (("repository", "other/repo"), ("main_sha", "main"), ("candidate_sha", None)):
            with self.subTest(key=key):
                payload = fixture()
                payload[key] = val
                with self.assertRaises(ValueError):
                    module.redact(payload)

    def test_rejects_unrecognized_evidence_status(self):
        payload = fixture()
        payload["vps_probe"]["status"] = "deployed"
        with self.assertRaises(ValueError):
            module.redact(payload)

    def test_cli_errors_never_echo_secrets(self):
        run = subprocess.run([sys.executable, str(SCRIPT)], input='{"token":"secret-marker"}',
                             text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 1)
        self.assertNotIn("secret-marker", run.stdout + run.stderr)
        self.assertFalse(json.loads(run.stdout)["release_authorized"])

    def test_duplicate_json_keys_are_rejected_without_echo(self):
        valid = json.dumps(fixture())
        duplicated = valid.replace('"repository": "Zennay/zCloud"', '"repository": "Zennay/zCloud", "repository": "secret-marker"')
        run = subprocess.run([sys.executable, str(SCRIPT)], input=duplicated,
                             text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 1)
        self.assertNotIn("secret-marker", run.stdout + run.stderr)
        self.assertFalse(json.loads(run.stdout)["release_authorized"])

    def test_oversized_input_is_bounded_and_denied(self):
        payload = json.dumps(fixture()) + ("secret-marker" * 6000)
        run = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                             text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 1)
        self.assertNotIn("secret-marker", run.stdout + run.stderr)
        self.assertFalse(json.loads(run.stdout)["mutation_performed"])

    def test_invalid_utf8_bytes_fail_closed(self):
        run = subprocess.run([sys.executable, str(SCRIPT)], input=b'{"token":"secret"}' + bytes([255]),
                             capture_output=True, check=False)
        self.assertEqual(run.returncode, 1)
        self.assertNotIn(b"secret", run.stdout + run.stderr)
        self.assertFalse(json.loads(run.stdout)["release_authorized"])

    def test_cli_success_is_still_non_authorizing(self):
        run = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(fixture()),
                             text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        response = json.loads(run.stdout)
        self.assertFalse(response["release_authorized"])
        self.assertFalse(response["mutation_performed"])
        self.assertNotIn("secret-", run.stdout)

if __name__ == "__main__":
    unittest.main()
