import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_host_pressure_report as pressure


class HostPressureReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-pressure-")
        self.proc = Path(self.tmp.name) / "proc"
        (self.proc / "pressure").mkdir(parents=True)
        self.now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
        self.write_proc()

    def tearDown(self):
        self.tmp.cleanup()

    def write_proc(
        self,
        *,
        mem_total=10_000_000,
        mem_available=6_000_000,
        swap_total=2_000_000,
        swap_free=1_500_000,
        load1=1.0,
        load5=0.8,
        load15=0.6,
        cpu_some=0.2,
        mem_some=0.0,
        mem_full=0.0,
        io_some=0.0,
        io_full=0.0,
        cpu_count=6,
    ):
        (self.proc / "meminfo").write_text(
            f"MemTotal: {mem_total} kB\n"
            f"MemAvailable: {mem_available} kB\n"
            f"SwapTotal: {swap_total} kB\n"
            f"SwapFree: {swap_free} kB\n",
            encoding="utf-8",
        )
        (self.proc / "loadavg").write_text(
            f"{load1} {load5} {load15} 1/100 42\n",
            encoding="utf-8",
        )
        stat = "cpu  1 2 3 4 5 6 7 8\n" + "".join(
            f"cpu{i} 1 2 3 4 5 6 7 8\n" for i in range(cpu_count)
        )
        (self.proc / "stat").write_text(stat, encoding="utf-8")
        (self.proc / "pressure" / "cpu").write_text(
            f"some avg10={cpu_some:.2f} avg60=0.00 avg300=0.00 total=1\n",
            encoding="utf-8",
        )
        (self.proc / "pressure" / "memory").write_text(
            f"some avg10={mem_some:.2f} avg60=0.00 avg300=0.00 total=1\n"
            f"full avg10={mem_full:.2f} avg60=0.00 avg300=0.00 total=1\n",
            encoding="utf-8",
        )
        (self.proc / "pressure" / "io").write_text(
            f"some avg10={io_some:.2f} avg60=0.00 avg300=0.00 total=1\n"
            f"full avg10={io_full:.2f} avg60=0.00 avg300=0.00 total=1\n",
            encoding="utf-8",
        )

    def report(self):
        return pressure.report(self.proc, now=self.now)

    def test_healthy_host_is_not_backpressured(self):
        payload = self.report()
        self.assertEqual("healthy", payload["assessment"]["state"])
        self.assertFalse(payload["assessment"]["requires_backpressure"])
        self.assertEqual(6, payload["metrics"]["cpu_count"])

    def test_busy_cpu_alone_is_explicitly_non_incident(self):
        self.write_proc(load1=12.0, cpu_some=35.0)
        payload = self.report()
        self.assertEqual("compute_busy", payload["assessment"]["state"])
        self.assertTrue(payload["assessment"]["compute_busy"])
        self.assertFalse(payload["assessment"]["memory_pressure"])
        self.assertFalse(payload["assessment"]["io_pressure"])
        self.assertFalse(payload["assessment"]["requires_backpressure"])

    def test_low_memory_requests_backpressure(self):
        self.write_proc(mem_available=700_000, swap_free=50_000)
        payload = self.report()
        self.assertEqual("memory_pressure", payload["assessment"]["state"])
        self.assertTrue(payload["assessment"]["requires_backpressure"])
        self.assertIn("memory_available_low", payload["assessment"]["reason_codes"])

    def test_memory_psi_requests_backpressure_even_with_free_memory(self):
        self.write_proc(mem_some=7.5, mem_full=1.5)
        payload = self.report()
        self.assertEqual("memory_pressure", payload["assessment"]["state"])
        self.assertIn("memory_psi_some", payload["assessment"]["reason_codes"])
        self.assertIn("memory_psi_full", payload["assessment"]["reason_codes"])

    def test_io_psi_requests_backpressure(self):
        self.write_proc(io_some=14.0, io_full=2.0)
        payload = self.report()
        self.assertEqual("io_pressure", payload["assessment"]["state"])
        self.assertTrue(payload["assessment"]["requires_backpressure"])

    def test_memory_and_io_pressure_are_mixed(self):
        self.write_proc(mem_available=500_000, io_full=2.0)
        payload = self.report()
        self.assertEqual("mixed_pressure", payload["assessment"]["state"])
        self.assertTrue(payload["assessment"]["memory_pressure"])
        self.assertTrue(payload["assessment"]["io_pressure"])

    def test_no_swap_is_not_itself_pressure(self):
        self.write_proc(swap_total=0, swap_free=0)
        payload = self.report()
        self.assertEqual("healthy", payload["assessment"]["state"])
        self.assertIsNone(payload["metrics"]["memory"]["swap_free_pct"])

    def test_output_contains_no_process_sensitive_fields(self):
        payload = self.report()
        encoded = json.dumps(payload, sort_keys=True).lower()
        for forbidden in ("argv", "environ", "cmdline", "pid", "processes"):
            self.assertNotIn(f'"{forbidden}"', encoded)

    def test_missing_or_malformed_proc_input_fails_closed(self):
        (self.proc / "pressure" / "io").unlink()
        with self.assertRaisesRegex(ValueError, "unreadable:io"):
            self.report()
        self.write_proc()
        (self.proc / "loadavg").write_text("not-a-load\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "loadavg_invalid"):
            self.report()

    def test_symlinked_input_fails_closed(self):
        original = self.proc / "meminfo"
        backup = self.proc / "meminfo-real"
        original.rename(backup)
        try:
            os.symlink(backup, original)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unsupported")
        with self.assertRaisesRegex(ValueError, "symlink_not_allowed:meminfo"):
            self.report()


if __name__ == "__main__":
    unittest.main(verbosity=2)
