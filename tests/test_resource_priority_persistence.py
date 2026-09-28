import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zcloud_enhancements_resource_test", ROOT / "enhancements.py")
enhancements = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(enhancements)


class ResourcePriorityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-resource-")
        self.original_resource_file = enhancements.RESOURCE_FILE
        self.original_units = enhancements.PROJECT_UNITS
        self.original_check_output = enhancements.subprocess.check_output
        enhancements.RESOURCE_FILE = Path(self.tmp.name) / "resource-policy.json"
        enhancements.RESOURCE_FILE.write_text(
            json.dumps({"cloud": {"priority": "normal"}}) + "\n",
            encoding="utf-8",
        )
        enhancements.PROJECT_UNITS = {"cloud": ["dummy.service"]}

    def tearDown(self):
        enhancements.RESOURCE_FILE = self.original_resource_file
        enhancements.PROJECT_UNITS = self.original_units
        enhancements.subprocess.check_output = self.original_check_output
        self.tmp.cleanup()

    def test_saved_priority_survives_live_apply_failure(self):
        def fail_apply(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args[0], output="sudo helper failed")

        enhancements.subprocess.check_output = fail_apply

        result = enhancements.set_priority("cloud", "high")

        saved = json.loads(enhancements.RESOURCE_FILE.read_text(encoding="utf-8"))
        self.assertEqual("high", saved["cloud"]["priority"])
        self.assertEqual("high", enhancements.load_resource_policy()["cloud"]["priority"])
        self.assertTrue(result["persisted"])
        self.assertFalse(result["applied"])
        self.assertIn("apply_error", result)

    def test_successful_apply_is_reported(self):
        enhancements.subprocess.check_output = lambda *args, **kwargs: "ok\n"

        result = enhancements.set_priority("cloud", "turbo")

        self.assertTrue(result["persisted"])
        self.assertTrue(result["applied"])
        self.assertEqual(3000, result["weight"])


if __name__ == "__main__":
    unittest.main()
