"""Independent, test-only fail-first acceptance for zSentinel recovery holds.

These tests intentionally fail on reviewed PR #1515 HEAD until its owner fixes
approval waits and duplicate safety keys. They never admit repair or make writes.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "zsentinel_diagnose.py"
spec = importlib.util.spec_from_file_location("zsentinel_pr1515_guard", SOURCE)
diagnostic = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(diagnostic)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def receipt(status: str = "queued") -> dict:
    return {
        "schema_version": 1,
        "observed_at": ts(-1),
        "projects": [{
            "id": "cloud",
            "github": {
                "job_status": status,
                "observed_at": ts(-1),
                "queued_at": ts(-45),
                "runner_assigned": False,
                "head_sha": "a" * 40,
                "run_id": 1234,
                "job_id": 5678,
            },
        }],
    }


def invoke(raw: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="zsentinel-hold-qa-") as folder:
        path = Path(folder) / "receipt.json"
        path.write_text(raw, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SOURCE), str(path)],
            text=True, capture_output=True, timeout=10, check=False,
        )


class ZSentinelPr1515HoldGuardTests(unittest.TestCase):
    def test_workflow_approval_wait_is_not_runner_outage(self):
        for status in ("waiting", "pending"):
            with self.subTest(status=status):
                report = diagnostic.diagnose(receipt(status), NOW)
                self.assertEqual([], report["incidents"])
                self.assertNotEqual("github_job_unassigned",
                                    report["projects"][0]["reason"])
                self.assertFalse(report["mutations_performed"])
                self.assertFalse(report["deploy_authorized"])

    def test_actually_queued_unassigned_job_remains_eligible_for_triage(self):
        report = diagnostic.diagnose(receipt("queued"), NOW)
        self.assertEqual("github_job_unassigned",
                         report["incidents"][0]["reason"])
        self.assertFalse(report["deploy_authorized"])

    def assert_duplicate_rejected(self, raw: str) -> None:
        result = invoke(raw)
        self.assertEqual(2, result.returncode, (result.stdout, result.stderr))
        self.assertIn("evidence rejected", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual("", result.stdout)
        self.assertNotIn("1234", result.stderr)
        self.assertNotIn("5678", result.stderr)

    def test_duplicate_human_hold_cannot_be_overwritten(self):
        sample = receipt()
        sample["projects"][0]["notion"] = {"human_gate": True}
        raw = json.dumps(sample)
        key = '"human_gate": true'
        self.assertEqual(1, raw.count(key))
        self.assert_duplicate_rejected(
            raw.replace(key, '"human_gate": true, "human_gate": false')
        )

    def test_duplicate_owner_hold_cannot_be_overwritten(self):
        sample = receipt()
        sample["projects"][0]["github"]["active_owner_pr"] = True
        raw = json.dumps(sample)
        key = '"active_owner_pr": true'
        self.assertEqual(1, raw.count(key))
        self.assert_duplicate_rejected(
            raw.replace(key, '"active_owner_pr": true, "active_owner_pr": false')
        )

    def test_duplicate_runtime_lock_cannot_be_overwritten(self):
        sample = receipt()
        sample["projects"][0]["runtime"] = {"ownership_locked": True}
        raw = json.dumps(sample)
        key = '"ownership_locked": true'
        self.assertEqual(1, raw.count(key))
        self.assert_duplicate_rejected(
            raw.replace(key, '"ownership_locked": true, "ownership_locked": false')
        )

    def test_duplicate_top_level_project_list_is_rejected(self):
        raw = json.dumps(receipt())
        self.assert_duplicate_rejected(raw.replace(
            '"projects": [', '"projects": [], "projects": [', 1,
        ))

    def test_valid_unique_key_cli_receipt_is_accepted(self):
        sample = receipt()
        result = invoke(json.dumps(sample))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("github_job_unassigned",
                         json.loads(result.stdout)["incidents"][0]["reason"])

    def test_valid_explicit_human_hold_remains_no_incident(self):
        sample = receipt()
        sample["projects"][0]["notion"] = {"human_gate": True}
        report = diagnostic.diagnose(sample, NOW)
        self.assertEqual([], report["incidents"])
        self.assertEqual("intentional_wait", report["projects"][0]["state"])


if __name__ == "__main__":
    unittest.main()
