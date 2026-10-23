from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_resource_scheduler_composition import base_payload


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_resource_scheduler_composition.py"


class ResourceSchedulerCompositionCliTests(unittest.TestCase):
    def run_cli(self, payload: object, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(path), "--json", *args],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_require_ready_accepts_ready_plan(self):
        result = self.run_cli(base_payload(), "--require-ready")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["decision_ready"])
        self.assertFalse(payload["runtime_mutation"])

    def test_require_ready_accepts_pressure_hold_as_valid_decision(self):
        payload = base_payload()
        payload["host"]["backpressure_required"] = True

        result = self.run_cli(payload, "--require-ready")

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "held")
        self.assertTrue(output["decision_ready"])
        self.assertEqual(output["reason"], "backpressure_required")
        self.assertEqual(output["borrow_admissions"], [])

    def test_require_ready_rejects_incomplete_evidence(self):
        payload = base_payload()
        payload["host"]["source_complete"] = False

        result = self.run_cli(payload, "--require-ready")

        self.assertEqual(result.returncode, 3)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "incomplete")
        self.assertFalse(output["decision_ready"])
        self.assertFalse(output["runtime_mutation"])

    def test_invalid_evidence_uses_distinct_fail_closed_exit(self):
        payload = base_payload()
        payload["host"]["capacity_cpu_cores"] = True

        result = self.run_cli(payload, "--require-ready")

        self.assertEqual(result.returncode, 2)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "invalid")
        self.assertFalse(output["decision_ready"])
        self.assertIn("capacity_cpu_cores_invalid", output["error"])
        self.assertFalse(output["runtime_mutation"])

    def test_symlink_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            link = root / "evidence.json"
            target.write_text(json.dumps(base_payload()), encoding="utf-8")
            link.symlink_to(target)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(link),
                    "--json",
                    "--require-ready",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "invalid")
        self.assertEqual(output["error"], "symlink_input_forbidden")


if __name__ == "__main__":
    unittest.main()
