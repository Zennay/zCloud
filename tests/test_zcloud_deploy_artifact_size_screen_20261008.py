"""Offline unit tests for the non-authorizing artifact size screen."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_deploy_artifact_size_screen_20261008.py"
spec = importlib.util.spec_from_file_location("size_screen", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SizeScreenTests(unittest.TestCase):
    def test_valid_metadata_never_authorizes(self):
        result = module.screen_sizes(1024, 8192)
        self.assertEqual(result["classification"], "METADATA_ONLY_UNVERIFIED")
        self.assertIs(result["authorized"], False)

    def test_bounds(self):
        self.assertEqual(module.screen_sizes(module.MAX_ARCHIVE_BYTES, module.MAX_EXPANDED_BYTES)["classification"], "METADATA_ONLY_UNVERIFIED")
        self.assertEqual(module.screen_sizes(module.MAX_ARCHIVE_BYTES + 1, 40000000)["classification"], "SIZE_LIMIT_EXCEEDED")
        self.assertEqual(module.screen_sizes(1, module.MAX_EXPANDED_BYTES + 1)["classification"], "SIZE_LIMIT_EXCEEDED")

    def test_invalid_types_and_order(self):
        for value in [None, False, True, 0, -1, "123", 1.0, [], {}]:
            with self.subTest(value=value):
                self.assertEqual(module.screen_sizes(value, 4096)["classification"], "INVALID_SIZE_METADATA")
        self.assertEqual(module.screen_sizes(100, 99)["classification"], "INCONSISTENT_SIZE_METADATA")

    def test_cli_always_denies(self):
        for args in [("10", "100"), ("0", "100"), ("01", "100"), ("+1", "100"), ("1", "１"), ("100", "10"), ("99999999999999", "999999999999999")]:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 2)
                self.assertIn('"authorized": false', proc.stdout)
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 2)

if __name__ == "__main__":
    unittest.main()
