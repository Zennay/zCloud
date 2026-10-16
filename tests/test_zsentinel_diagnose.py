"""zSentinel read-only detection: plausible incidents, hold gates, stale and malicious evidence."""
from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zsentinel_diagnose", ROOT / "scripts" / "zsentinel_diagnose.py")
sentinel = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sentinel)

NOW = datetime(2026, 10, 16, 19, 45, tzinfo=timezone.utc)
SHA = "a" * 40


def ts(**kwargs):
    return (NOW + timedelta(**kwargs)).isoformat()


def project(**kwargs):
    return {"id": "cloud", **kwargs}


def check(entry):
    return sentinel.diagnose(
        {"schema_version": 1, "observed_at": ts(minutes=-1), "projects": [entry]},
        NOW,
    )


class ZSentinelClassifierTests(unittest.TestCase):
    def test_runner_unassigned_is_incident_with_stable_dedup(self):
        entry = project(github={"job_status": "queued", "observed_at": ts(minutes=-1),
                                "queued_at": ts(minutes=-60), "runner_assigned": False,
                                "head_sha": SHA})
        actual = check(entry)
        self.assertFalse(actual["mutations_performed"])
        self.assertFalse(actual["deploy_authorized"])
        self.assertEqual("github_job_unassigned", actual["incidents"][0]["reason"])
        self.assertIn("independent route", actual["incidents"][0]["next_action"])
        self.assertEqual(actual["incidents"][0]["incident_id"], check(entry)["incidents"][0]["incident_id"])

    def test_future_market_wait_is_not_accidentally_unblocked(self):
        actual = check(project(notion={"wait_until": ts(days=2)},
                               github={"job_status": "queued", "observed_at": ts(minutes=-1),
                                       "queued_at": ts(hours=-12), "runner_assigned": False}))
        self.assertEqual("intentional_wait", actual["projects"][0]["state"])
        self.assertEqual([], actual["incidents"])

    def test_human_gate_preempts_failure(self):
        self.assertEqual("intentional_wait", check(project(
            notion={"human_gate": True},
            github={"job_status": "completed", "observed_at": ts(minutes=-1),
                    "conclusion": "failure"}))["projects"][0]["state"])

    def test_locked_owner_preempts_repair(self):
        self.assertEqual("owner_held", check(project(
            github={"active_owner_pr": True, "job_status": "completed",
                    "observed_at": ts(minutes=-1), "conclusion": "failure"}))["projects"][0]["state"])

    def test_workflow_failures_are_incidents(self):
        item = check(project(github={"job_status": "completed", "conclusion": "failure",
                                     "observed_at": ts(minutes=-1), "head_sha": SHA}))["incidents"][0]
        self.assertEqual("github_workflow_failed", item["reason"])

    def test_recent_queue_is_not_a_false_positive(self):
        item = check(project(github={"job_status": "queued", "observed_at": ts(minutes=-1),
                                     "queued_at": ts(minutes=-5), "runner_assigned": False}))["projects"][0]
        self.assertEqual("in_progress", item["state"])

    def test_stale_snapshot_rejected(self):
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            sentinel.diagnose({"schema_version": 1,
                               "observed_at": ts(hours=-3), "projects": [project()]}, NOW)

    def test_stale_github_signal_does_not_raise_incident(self):
        item = check(project(github={"job_status": "completed", "conclusion": "failure",
                                     "observed_at": ts(hours=-2)}))["projects"][0]
        self.assertEqual("unknown", item["state"])

    def test_runtime_heartbeat_absent_is_detected_only_while_running(self):
        entry = project(runtime={"execution_state": "running",
                                 "heartbeat_at": ts(minutes=-25),
                                 "heartbeat_interval_seconds": 30})
        self.assertEqual("runtime_progress_missing", check(entry)["incidents"][0]["reason"])
        self.assertEqual([], check(project(runtime={"execution_state": "paused"}))["incidents"])

    def test_recent_heartbeat_not_automatically_success(self):
        item = check(project(runtime={"execution_state": "running",
                                      "heartbeat_at": ts(minutes=-1),
                                      "heartbeat_interval_seconds": 60}))["projects"][0]
        self.assertEqual("in_progress", item["state"])

    def test_green_ci_not_verified_live_recovery(self):
        item = check(project(github={"job_status": "completed", "conclusion": "success",
                                     "observed_at": ts(minutes=-1)}))["projects"][0]
        self.assertEqual("unknown", item["state"])

    def test_clock_spoof_rejected(self):
        with self.assertRaisesRegex(ValueError, "future timestamp"):
            check(project(github={"job_status": "queued", "observed_at": ts(minutes=10),
                                  "queued_at": ts(minutes=-90), "runner_assigned": False}))

    def test_duplicate_project_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            sentinel.diagnose({"schema_version": 1, "observed_at": ts(minutes=-1),
                               "projects": [project(), project()]}, NOW)

    def test_bounded_snapshot_and_invalid_status(self):
        with self.assertRaisesRegex(ValueError, "bounded"):
            sentinel.diagnose({"schema_version": 1, "observed_at": ts(minutes=-1),
                               "projects": []}, NOW)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            check(project(github={"job_status": "everything_fine", "observed_at": ts(minutes=-1)}))

    def test_no_raw_evidence_leaks_into_report(self):
        item = check(project(runtime={"secret": "never-output", "conversation_url": "secret://internal"}))
        blob = json.dumps(item)
        self.assertNotIn("never-output", blob)
        self.assertNotIn("secret://internal", blob)


if __name__ == "__main__":
    unittest.main()
