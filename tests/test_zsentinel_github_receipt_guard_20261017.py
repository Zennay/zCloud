"""Cross-project run/job provenance and non-authorizing wait classification."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zsentinel_github_receipt_guard_20261017.py"
spec = importlib.util.spec_from_file_location("receipt_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)


def stamp(delta=0):
    return (NOW + timedelta(minutes=delta)).isoformat()


def receipt(status="queued", **job):
    result = {
        "schema_version": 1, "project_id": "cloud", "repository": "Zennay/zCloud",
        "observed_at": stamp(-1),
        "workflow_run": {"id": 123, "head_sha": "a"*40, "run_attempt": 1,
                         "repository": "Zennay/zCloud"},
        "job": {"id": 456, "run_id": 123, "head_sha": "a"*40,
                "repository": "Zennay/zCloud", "status": status,
                "conclusion": "success" if status == "completed" else None,
                "created_at": stamp(-46), "runner_id": 0, "runner_name": None}
    }
    result["job"].update(job)
    return result


def check(data):
    return guard.inspect_receipt(data, NOW)


class RunJobEvidenceTests(unittest.TestCase):
    def test_waiting_pending_requested_never_become_runner_outages(self):
        for status in ("waiting", "pending", "requested"):
            for age in (31, 60, 600):
                with self.subTest(status=status, age=age):
                    doc = receipt(status, created_at=stamp(-age))
                    output = check(doc)
                    self.assertEqual("intentional_or_scheduler_wait", output["state"])
                    self.assertFalse(output["repair_authorized"])

    def test_only_unassigned_queued_runs_need_independent_triage(self):
        self.assertEqual("needs_independent_triage", check(receipt())["state"])
        self.assertEqual("in_progress", check(receipt(created_at=stamp(-20)))["state"])
        self.assertEqual("in_progress", check(receipt(
            runner_id=23, runner_name="zcloud-vps-1"))["state"])

    def test_positive_job_status_never_proves_runtime_recovery(self):
        for conclusion in ("success", "skipped", "neutral", "cancelled", "stale"):
            with self.subTest(conclusion=conclusion):
                output = check(receipt("completed", conclusion=conclusion))
                self.assertEqual("unknown", output["state"])
                self.assertFalse(output["recovery_verified"])
                self.assertFalse(output["deploy_authorized"])

    def test_failure_is_triage_not_permission_to_repair(self):
        for conclusion in ("failure", "action_required", "timed_out"):
            output = check(receipt("completed", conclusion=conclusion))
            self.assertEqual("terminal_job_failure", output["reason"])
            self.assertFalse(output["repair_authorized"])
            self.assertFalse(output["mutations_performed"])

    def test_retries_and_distinct_jobs_have_distinct_redacted_identities(self):
        baseline = check(receipt())["evidence_fingerprint"]
        for node, key, value in (("job", "id", 457),
                                 ("workflow_run", "run_attempt", 2),
                                 ("workflow_run", "id", 124)):
            doc = receipt()
            doc[node][key] = value
            if node == "workflow_run" and key == "id":
                doc["job"]["run_id"] = value
            self.assertNotEqual(baseline, check(doc)["evidence_fingerprint"])
        self.assertNotIn("123", str(check(receipt())))
        self.assertNotIn("456", str(check(receipt())))

    def test_mismatched_run_sha_repo_or_attempt_rejected(self):
        for node, key, value in (
            ("job", "run_id", 124), ("job", "head_sha", "b"*40),
            ("job", "repository", "Other/Repo"), ("workflow_run", "repository", "Other/Repo"),
            ("job", "run_attempt", 2),
        ):
            doc = receipt()
            doc[node][key] = value
            with self.subTest(node=node, key=key):
                with self.assertRaises(ValueError):
                    check(doc)

    def test_boolean_and_unhashable_provenance_rejected(self):
        for key, value in (("id", True), ("id", []), ("head_sha", {}),
                           ("run_attempt", 0), ("run_attempt", 1.0)):
            doc = receipt()
            doc["workflow_run"][key] = value
            with self.subTest(key=key, value=repr(value)):
                with self.assertRaises(ValueError):
                    check(doc)

    def test_contradictory_runner_and_lifecycle_refused(self):
        for key, value in (("runner_id", True), ("runner_id", -1),
                           ("runner_name", "fake-runner"), ("status", []),
                           ("status", "unknown"), ("conclusion", "failure")):
            doc = receipt()
            doc["job"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    check(doc)
        with self.assertRaises(ValueError):
            check(receipt("completed", conclusion=None))

    def test_stale_and_future_receipts_refused(self):
        for observation in (stamp(-31), stamp(3)):
            doc = receipt()
            doc["observed_at"] = observation
            with self.assertRaises(ValueError):
                check(doc)
        doc = receipt(created_at=stamp(4))
        with self.assertRaises(ValueError):
            check(doc)

    def test_cli_refuses_symlink_fifo_duplicate_keys_and_oversize(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            good = root / "receipt.json"
            good.write_text(json.dumps(receipt()), encoding="utf-8")
            linked = root / "link.json"
            linked.symlink_to(good)
            cases = [linked]
            oversized = root / "big.json"
            oversized.write_bytes(b" " * 256001)
            cases.append(oversized)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            cases.append(duplicate)
            if hasattr(os, "mkfifo"):
                fifo = root / "pipe.json"
                os.mkfifo(fifo)
                cases.append(fifo)
            for path in cases:
                with self.subTest(path=path.name):
                    p = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                       capture_output=True, text=True, timeout=5)
                    self.assertEqual(2, p.returncode, p.stdout + p.stderr)
                    self.assertEqual("", p.stdout)
                    self.assertNotIn("Traceback", p.stderr)

    def test_exact_boundary_30min_and_clock_skew(self):
        doc = receipt(created_at=stamp(-31))
        self.assertEqual("needs_independent_triage", check(doc)["state"])
        doc = receipt(created_at=stamp(-30))
        self.assertEqual("in_progress", check(doc)["state"])
        doc = receipt(created_at=stamp(-29))
        self.assertEqual("in_progress", check(doc)["state"])


if __name__ == "__main__":
    unittest.main()
