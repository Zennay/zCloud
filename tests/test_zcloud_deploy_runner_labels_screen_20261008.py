"""Offline deny-only deployment runner label screen regressions."""
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from zcloud_deploy_runner_labels_screen_20261008 import screen

class RunnerLabelScreenTests(unittest.TestCase):
    def test_matching_set_is_still_not_verified(self):
        for labels in (["self-hosted", "zcloud", "vps"], ["vps", "self-hosted", "zcloud"]):
            outcome = screen(labels)
            self.assertEqual(outcome["classification"], "SYNTAX_ONLY_UNVERIFIED")
            self.assertFalse(any(value for key, value in outcome.items() if key != "classification"))

    def test_unsafe_inputs_fail_closed(self):
        cases = [None, {}, "self-hosted,zcloud,vps", ["self-hosted", "vps"],
                 ["self-hosted", "zcloud", "vps", "production"],
                 ["self-hosted", "zcloud", "zcloud"], ["self-hosted", "zcloud", "VPS"],
                 ["self-hosted", "zcloud", "vps "], ["self-hosted", "zcloud", 1],
                 [True, "zcloud", "vps"]]
        for labels in cases:
            with self.subTest(labels=labels):
                result = screen(labels)
                self.assertEqual(result["classification"], "INVALID_LABEL_SET")
                self.assertFalse(result["deploy_authorized"])
                self.assertFalse(result["mutation_performed"])

    def test_cli_never_admits(self):
        script = ROOT / "scripts" / "zcloud_deploy_runner_labels_screen_20261008.py"
        for raw in ('["self-hosted","zcloud","vps"]', '{"labels":[]}', 'not-json'):
            result = subprocess.run([sys.executable, str(script), raw],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["deploy_authorized"])

if __name__ == "__main__":
    unittest.main()
