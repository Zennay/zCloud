"""Contract tests for the non-authorizing deploy-ops static screen."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy_ops_pr_recovery_static_screen_20261008.py"
spec = importlib.util.spec_from_file_location("screen", SCRIPT)
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)

class PRRecoveryScreenTest(unittest.TestCase):
    def test_pr_self_hosted_privileged_flagged(self):
        body = "on:\n  pull_request:\n    branches: [main]\njobs:\n  recover:\n    runs-on: self-hosted\n    steps:\n      - run: sudo systemctl restart zcloud\n"
        result = screen.inspect(body)
        self.assertTrue(result["review_required"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["recovery_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_pr_target_self_hosted_flagged_without_privilege(self):
        result = screen.inspect("on:\n  pull_request_target:\njobs:\n  check:\n    runs-on: self-hosted\n")
        self.assertTrue(result["review_required"])

    def test_hosted_pr_without_privileged_cues_not_flagged(self):
        result = screen.inspect("on:\n  pull_request:\njobs:\n  check:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(result["review_required"])
        self.assertFalse(result["deploy_authorized"])

    def test_manual_self_hosted_not_auto_authorized(self):
        result = screen.inspect("on:\n  workflow_dispatch:\njobs:\n  repair:\n    runs-on: self-hosted\n    steps:\n      - run: sudo systemctl restart zcloud\n")
        self.assertFalse(result["review_required"])
        self.assertFalse(result["recovery_authorized"])

    def test_multiline_runner_requires_review(self):
        body = "on:\\n  pull_request:\\njobs:\\n  check:\\n    runs-on:\\n      - self-hosted\\n      - linux\\n"
        result = screen.inspect(body)
        self.assertTrue(result["review_required"])
        self.assertIn("pr_trigger_self_hosted_manual_review", result["signals"])

    def test_pr_self_hosted_without_privilege_still_requires_review(self):
        body = "on:\\n  pull_request:\\njobs:\\n  check:\\n    runs-on: [self-hosted, linux]\\n"
        result = screen.inspect(body)
        self.assertTrue(result["review_required"])
        self.assertFalse(result["recovery_authorized"])

    def test_invalid_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, str(SCRIPT), str(pathlib.Path(tmp) / "missing.yml")],
                                  text=True, capture_output=True, check=False)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('"invalid_input"', proc.stdout)
            self.assertIn('"recovery_authorized": false', proc.stdout)

if __name__ == "__main__":
    unittest.main()
