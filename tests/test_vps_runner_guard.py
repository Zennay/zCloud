import json
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_vps_runner_guard import validate_runner


class VpsRunnerGuardTests(unittest.TestCase):
    def policy(self) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({
            "runner": {
                "host": "vps-bb300bba",
                "forbidden_name_tokens": ["haxlab"],
            }
        }, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        return Path(tmp.name)

    def test_accepts_permanent_zcloud_runner(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="zcloud-vps-1",
        )
        self.assertTrue(result["ok"])
        self.assertEqual([], result["errors"])

    def test_rejects_temporary_haxlab_runner_even_on_same_host(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="vps-bb300bba-haxlab",
        )
        self.assertFalse(result["ok"])
        self.assertIn("forbidden_runner_name:haxlab", result["errors"])

    def test_rejects_wrong_host(self):
        result = validate_runner(
            self.policy(),
            hostname="another-host",
            runner_name="zcloud-vps-1",
        )
        self.assertFalse(result["ok"])
        self.assertIn("host_mismatch", result["errors"])

    def test_rejects_missing_runner_name(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="",
        )
        self.assertFalse(result["ok"])
        self.assertIn("runner_name_missing", result["errors"])


if __name__ == "__main__":
    unittest.main()
