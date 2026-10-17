"""Adversarial offline tests for the queue-watchdog observation boundary."""
import copy
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_watchdog_queue_admission_20261014_w1.py"
SPEC = importlib.util.spec_from_file_location("queue_admission_w1", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40
NOW = datetime(2026, 10, 14, 3, tzinfo=timezone.utc)


def job(ident, *, status="queued", labels=None):
    return {"id": ident, "status": status,
            "labels": labels if labels is not None else ["self-hosted", "zcloud", "vps"]}


def run(ident, *, workflow=8, event="push", jobs=None):
    return {"id": ident, "repository": "Zennay/zCloud", "head_sha": SHA,
            "head_branch": "main", "event": event, "workflow_id": workflow,
            "status": "queued", "jobs_complete": True,
            "jobs": jobs if jobs is not None else [job(ident + 1000)]}


def fixture(*runs):
    return {"repository": "Zennay/zCloud", "main_sha_before": SHA,
            "main_sha_after": SHA, "observed_at": "2026-10-14T02:59:40Z",
            "run_pages": [{"runs": list(runs), "has_next": False}]}


class TestWatchdogQueueAdmission(unittest.TestCase):
    def classify(self, snapshot, **options):
        return MODULE.assess(snapshot, expected_main_sha=options.get("sha", SHA),
                             now=options.get("now", NOW))

    def test_empty_complete_queue_is_noop(self):
        result = self.classify(fixture())
        self.assertEqual(result["decision"], "NOOP")
        self.assertFalse(result["inventory_complete"])
        self.assertTrue(result["submitted_pagination_consistent"])
        self.assertFalse(result["mutation_authorized"])

    def test_same_workflow_duplicate_is_review_only_not_cancel(self):
        result = self.classify(fixture(run(4), run(8)))
        self.assertEqual(result["decision"], "REVIEW_ONLY")
        self.assertEqual(result["candidate_duplicate_groups"], [[4, 8]])
        self.assertEqual(result["cancel_run_ids"], [])
        self.assertFalse(result["mutation_authorized"])

    def test_page_two_duplicate_observed_without_cancellation(self):
        snap = fixture(run(4))
        snap["run_pages"] = [{"runs": [run(4)], "has_next": True},
                             {"runs": [run(8)], "has_next": False}]
        self.assertEqual(self.classify(snap)["candidate_duplicate_groups"], [[4, 8]])

    def test_truncated_page_must_deny(self):
        snap = fixture(run(4))
        snap["run_pages"][0]["has_next"] = True
        self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_bad_page_boundary_must_deny(self):
        snap = fixture(run(4))
        snap["run_pages"].append({"runs": [run(8)], "has_next": False})
        self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_cross_workflow_runs_not_duplicates(self):
        self.assertEqual(self.classify(fixture(run(4), run(8, workflow=9)))["decision"], "NOOP")

    def test_cross_event_runs_not_duplicates(self):
        self.assertEqual(self.classify(fixture(run(4), run(8, event="workflow_dispatch")))["decision"], "NOOP")

    def test_active_job_never_cancelled_or_grouped(self):
        active = run(4, jobs=[job(1004), job(1005, status="in_progress")])
        self.assertEqual(self.classify(fixture(active, run(8)))["decision"], "NOOP")

    def test_fork_and_wrong_branch_deny(self):
        for key, value in (("repository", "evil/fork"), ("head_branch", "worker/other"),
                           ("head_sha", "b" * 40), ("event", "pull_request")):
            with self.subTest(key=key):
                snap = fixture(run(4))
                snap["run_pages"][0]["runs"][0][key] = value
                self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_missing_job_page_deny(self):
        snap = fixture(run(4))
        snap["run_pages"][0]["runs"][0]["jobs_complete"] = False
        self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_other_runner_label_not_cancelled(self):
        result = self.classify(fixture(run(4, jobs=[job(1004, labels=["self-hosted", "ftmo-research"])]), run(8)))
        self.assertEqual(result["decision"], "NOOP")

    def test_duplicate_run_id_denied(self):
        self.assertEqual(self.classify(fixture(run(4), run(4)))["decision"], "DENY")

    def test_duplicate_job_id_denied(self):
        self.assertEqual(self.classify(fixture(run(4), run(8, jobs=[job(1004)])))["decision"], "DENY")

    def test_bad_numeric_ids_denied(self):
        for ident in (True, 0, -1, "3"):
            with self.subTest(ident=ident):
                snap = fixture(run(4))
                snap["run_pages"][0]["runs"][0]["id"] = ident
                self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_stale_future_missing_and_offset_clock_denied(self):
        for timestamp in ("2026-10-14T02:58:00Z", "2026-10-14T03:01:00Z",
                          "2026-10-14T03:00:00+00:00", "", None):
            with self.subTest(timestamp=timestamp):
                snap = fixture()
                snap["observed_at"] = timestamp
                self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_main_changes_at_either_bound_denied(self):
        for key in ("main_sha_before", "main_sha_after"):
            snap = fixture()
            snap[key] = "b" * 40
            self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_missing_labels_denied(self):
        snap = fixture(run(4, jobs=[job(1004, labels=None)]))
        snap["run_pages"][0]["runs"][0]["jobs"][0]["labels"] = None
        self.assertEqual(self.classify(snap)["decision"], "DENY")

    def test_oversized_unverified_page_denied(self):
        snap = fixture()
        snap["run_pages"][0]["runs"] = [run(i) for i in range(1, 102)]
        self.assertEqual(self.classify(snap)["decision"], "DENY")


    def test_unhashable_run_event_and_status_are_denied_without_exceptions(self):
        for field in ("event", "status"):
            for invalid in ([], {}, ["queued"], {"queued": True}, None, True):
                with self.subTest(field=field, invalid=invalid):
                    row = run(4)
                    row[field] = invalid
                    report = self.classify(fixture(row))
                    self.assertEqual(report["decision"], "DENY")
                    self.assertFalse(report["mutation_authorized"])
                    self.assertEqual(report["cancel_run_ids"], [])

    def test_unhashable_job_status_denied_without_exceptions(self):
        for invalid in ([], {}, ["queued"], {"status": "queued"}, None, True):
            with self.subTest(invalid=invalid):
                row = run(4, jobs=[job(1004, status=invalid)])
                self.assertEqual(self.classify(fixture(row))["decision"], "DENY")

    def test_invalid_clock_object_denied_safely(self):
        for wrong_now in (None, [], {}, "2026-10-14T03:00:00Z", 0):
            with self.subTest(now=wrong_now):
                report = self.classify(fixture(), now=wrong_now)
                self.assertEqual(report["decision"], "DENY")
                self.assertEqual(report["reason"], "invalid_now")

    def test_cli_unhashable_adversarial_json_never_crashes_or_leaks(self):
        for field, invalid in (("event", ["secret_event"]), ("status", {"secret": True}),
                               ("job_status", ["secret_job"])):
            with self.subTest(field=field):
                row = run(4)
                if field == "job_status":
                    row["jobs"][0]["status"] = invalid
                else:
                    row[field] = invalid
                payload = fixture(row)
                payload["observed_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                with tempfile.TemporaryDirectory() as directory:
                    input_path = Path(directory) / "snapshot.json"
                    input_path.write_text(json.dumps(payload), encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(PATH), "--snapshot", str(input_path),
                         "--expected-main-sha", SHA], text=True,
                        capture_output=True, timeout=8,
                    )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)["decision"], "DENY")
                self.assertEqual(result.stderr, "")
                self.assertNotIn("secret", result.stdout)

    def test_cli_rejects_duplicate_json_keys_without_exposing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "case.json"
            snapshot.write_text('{"repository":"Zennay/zCloud","repository":"evil"}', encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(PATH), "--snapshot", str(snapshot), "--expected-main-sha", SHA],
                capture_output=True, text=True, timeout=8,
            )
        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "DENY")
        self.assertEqual(report["reason"], "invalid_snapshot_file")
        self.assertNotIn("evil", completed.stdout)

    def test_cli_rejects_oversized_input_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "huge.json"
            snapshot.write_bytes(b"x" * (2 * 1024 * 1024 + 5))
            completed = subprocess.run(
                [sys.executable, str(PATH), "--snapshot", str(snapshot), "--expected-main-sha", SHA],
                capture_output=True, text=True, timeout=8,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["reason"], "invalid_snapshot_file")

    def test_cli_never_authorizes_on_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "case.json"
            payload = fixture(run(4), run(8))
            payload["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(PATH), "--snapshot", str(snapshot), "--expected-main-sha", SHA],
                capture_output=True, text=True, timeout=8,
            )
        report = json.loads(completed.stdout)
        self.assertEqual(report["decision"], "REVIEW_ONLY")
        self.assertEqual(report["cancel_run_ids"], [])
        self.assertFalse(report["mutation_authorized"])
        self.assertFalse(report["inventory_complete"])


if __name__ == "__main__":
    unittest.main()
