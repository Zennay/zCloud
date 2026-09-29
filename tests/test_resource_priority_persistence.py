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
        self.original_unit_numbers = enhancements._unit_numbers
        self.original_cpu_count = enhancements.os.cpu_count
        self.original_host_counter = enhancements._host_cpu_counter
        self.original_cgroup_cpu = enhancements._cgroup_cpu_nsec
        enhancements._RESOURCE_PREV.clear()
        enhancements._RESOURCE_HOST_PREV = None
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
        enhancements._unit_numbers = self.original_unit_numbers
        enhancements.os.cpu_count = self.original_cpu_count
        enhancements._host_cpu_counter = self.original_host_counter
        enhancements._cgroup_cpu_nsec = self.original_cgroup_cpu
        enhancements._RESOURCE_PREV.clear()
        enhancements._RESOURCE_HOST_PREV = None
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

    def test_project_cpu_is_normalized_to_host_share(self):
        samples = iter([
            (0, 0, "active"),
            (2_000_000_000, 0, "active"),
        ])
        enhancements._unit_numbers = lambda unit: next(samples)
        enhancements.os.cpu_count = lambda: 4
        enhancements._host_cpu_counter = lambda: (1000, 500)
        times = iter([10.0, 12.0])
        original_monotonic = enhancements.time.monotonic
        enhancements.time.monotonic = lambda: next(times)
        try:
            enhancements.resource_snapshot()
            result = enhancements.resource_snapshot()
        finally:
            enhancements.time.monotonic = original_monotonic
        # 2 CPU-seconds over 2 wall-seconds = one saturated core = 25% of a 4-core VPS.
        self.assertEqual(25.0, result["cloud"]["cpu_percent"])
        self.assertEqual("host_share", result["cloud"]["mode"])

    def test_summary_reports_unattributed_host_cpu(self):
        samples = iter([
            (0, 0, "active"),
            (2_000_000_000, 0, "active"),
        ])
        enhancements._unit_numbers = lambda unit: next(samples)
        enhancements.os.cpu_count = lambda: 4
        host_samples = iter([(1000, 500), (1400, 600)])
        enhancements._host_cpu_counter = lambda: next(host_samples)
        times = iter([10.0, 12.0])
        original_monotonic = enhancements.time.monotonic
        enhancements.time.monotonic = lambda: next(times)
        try:
            enhancements.resource_snapshot()
            result = enhancements.resource_snapshot()
        finally:
            enhancements.time.monotonic = original_monotonic
        summary = result["_summary"]
        self.assertEqual(75.0, summary["host_cpu_percent"])
        self.assertEqual(25.0, summary["attributed_cpu_percent"])
        self.assertEqual(50.0, summary["unattributed_cpu_percent"])

    def test_cgroup_cpu_usage_overrides_zero_systemd_accounting(self):
        raw = "CPUUsageNSec=0\nMemoryCurrent=1234\nActiveState=active\nControlGroup=/system.slice/demo.service\n"
        enhancements.subprocess.check_output = lambda *args, **kwargs: raw
        enhancements._cgroup_cpu_nsec = lambda group: 7_000_000_000
        cpu, mem, state = enhancements._unit_numbers("demo.service")
        self.assertEqual(7_000_000_000, cpu)
        self.assertEqual(1234, mem)
        self.assertEqual("active", state)


if __name__ == "__main__":
    unittest.main()
