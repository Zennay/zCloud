import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import zcloud_backpressure_advice as advice


class BackpressureAdviceTests(unittest.TestCase):
    def test_direct_cli_entrypoint_imports_successfully(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "zcloud_backpressure_advice.py"), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("backpressure", result.stdout.lower())

    def test_memory_or_io_pressure_has_priority_over_useful_scaling(self):
        result = advice.decide(
            {"state": "useful_scaling"},
            {"state": "memory_pressure", "requires_backpressure": True},
        )
        self.assertEqual("backpressure_recommended", result["decision"])
        self.assertTrue(result["apply_backpressure"])
        self.assertEqual("block_new_capacity", result["recommended_action"])

    def test_diminishing_returns_recommends_reducing_parallelism(self):
        result = advice.decide(
            {"state": "diminishing_returns"},
            {"state": "healthy", "requires_backpressure": False},
        )
        self.assertEqual("backpressure_recommended", result["decision"])
        self.assertEqual("reduce_parallelism_candidate", result["recommended_action"])

    def test_cpu_busy_alone_does_not_trigger_backpressure(self):
        result = advice.decide(
            {"state": "useful_scaling"},
            {"state": "compute_busy", "requires_backpressure": False},
        )
        self.assertEqual("no_backpressure", result["decision"])
        self.assertFalse(result["apply_backpressure"])
        self.assertIn("cpu_busy_without_memory_io_pressure", result["reason_codes"])

    def test_inconclusive_worker_evidence_does_not_guess(self):
        result = advice.decide(
            {"state": "inconclusive"},
            {"state": "healthy", "requires_backpressure": False},
        )
        self.assertEqual("insufficient_data", result["decision"])
        self.assertFalse(result["apply_backpressure"])

    def test_missing_worker_evidence_does_not_guess(self):
        result = advice.decide(
            {"state": "insufficient_data"},
            {"state": "healthy", "requires_backpressure": False},
        )
        self.assertEqual("insufficient_data", result["decision"])

    def test_report_emits_only_bounded_evidence(self):
        now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
        scaling_payload = {
            "projects": [
                {
                    "project_id": "cloud",
                    "desired_workers": 2,
                    "completed_generations": 5,
                    "assessment": {
                        "state": "useful_scaling",
                        "extra_vs_primary_throughput_ratio": 0.8,
                        "extra_idle_blocked_pct": 20.0,
                        "reason": "raw free text must not be forwarded",
                    },
                    "workers": [{"sensitive": "do not expose"}],
                }
            ]
        }
        pressure_payload = {
            "assessment": {
                "state": "compute_busy",
                "requires_backpressure": False,
                "compute_busy": True,
                "memory_pressure": False,
                "io_pressure": False,
                "interpretation": "raw explanation must not be forwarded",
            }
        }
        with patch.object(advice.scaling, "report", return_value=scaling_payload), patch.object(
            advice.pressure, "report", return_value=pressure_payload
        ):
            payload = advice.report(
                Path("/tmp/history.db"),
                project="cloud",
                hours=12,
                proc_root=Path("/proc"),
                now=now,
            )
        self.assertEqual("no_backpressure", payload["assessment"]["decision"])
        self.assertEqual(2, payload["evidence"]["desired_workers"])
        serialized = str(payload)
        self.assertNotIn("raw free text", serialized)
        self.assertNotIn("do not expose", serialized)
        self.assertNotIn("raw explanation", serialized)

    def test_report_rejects_unsafe_project_identifier(self):
        with self.assertRaisesRegex(ValueError, "invalid_project"):
            advice.report(Path("/tmp/history.db"), project="../cloud")

    def test_duplicate_project_evidence_fails_closed(self):
        duplicate = {
            "projects": [
                {"project_id": "cloud", "assessment": {"state": "single_worker"}},
                {"project_id": "cloud", "assessment": {"state": "single_worker"}},
            ]
        }
        with patch.object(advice.scaling, "report", return_value=duplicate), patch.object(
            advice.pressure,
            "report",
            return_value={"assessment": {"state": "healthy", "requires_backpressure": False}},
        ):
            with self.assertRaisesRegex(ValueError, "duplicate_project_scaling_evidence"):
                advice.report(Path("/tmp/history.db"), project="cloud")


if __name__ == "__main__":
    unittest.main(verbosity=2)
