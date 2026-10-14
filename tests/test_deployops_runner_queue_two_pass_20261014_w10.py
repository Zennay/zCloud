"""Second-pass safety regression for hosted zCloud runner queue observations.

Every fixture is in memory; no Github API, VPS runner or queue mutation occurs.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from scripts import zcloud_runner_queue_watch_20261014 as watch
from tests.test_zcloud_runner_queue_watch_20261014 import sample


class TwoPassRunnerAdmissionTests(TestCase):
    def setUp(self):
        self.runs = []
        self.jobs = {}
        now = datetime.now(timezone.utc)
        for rid in (1, 2, 3):
            run, job = sample(rid, age=1800)
            timestamp = (now - timedelta(minutes=35)).isoformat()
            run["created_at"] = timestamp
            job["created_at"] = timestamp
            self.runs.append(run)
            self.jobs[rid] = job
        self.confirm_runs = deepcopy(self.runs)
        self.confirm_jobs = deepcopy(self.jobs)
        self.total_count = 3
        self.confirm_total_count = 3
        self.confirm_job_count = {}
        self.calls = {}

    def read(self, url, _token, _opener):
        count = self.calls.get(url, 0)
        self.calls[url] = count + 1
        second = count > 0
        if url == watch.URL:
            return deepcopy({
                "workflow_runs": self.confirm_runs if second else self.runs,
                "total_count": self.confirm_total_count if second else self.total_count,
            })
        for rid in (1, 2, 3):
            expected = (f"https://api.github.com/repos/{watch.REPO}"
                        f"/actions/runs/{rid}/jobs?per_page=100")
            if url == expected:
                job = (self.confirm_jobs if second else self.jobs)[rid]
                return {"jobs": [deepcopy(job)],
                        "total_count": (self.confirm_job_count.get(rid, 1)
                                        if second else 1)}
        raise AssertionError("Unexpected endpoint")

    def collect(self):
        with patch.object(watch, "_get", side_effect=self.read):
            return watch.collect("test-token")

    def test_persistent_queue_gap_requires_exact_second_snapshot(self):
        report = self.collect()
        self.assertEqual(report["classification"], "POSSIBLE_LISTENER_GAP")
        self.assertEqual(report["stale_unassigned"], 3)
        self.assertIs(report["mutation_authorized"], False)
        self.assertIs(report["runner_offline_verified"], False)
        self.assertEqual(self.calls[watch.URL], 2)
        self.assertEqual(sum(self.calls.values()), 8)

    def test_job_assigned_while_first_pass_finishes_retracts_warning(self):
        self.confirm_jobs[2].update({
            "status": "in_progress",
            "runner_id": 21,
            "runner_name": "zcloud-vps-1",
        })
        with self.assertRaisesRegex(watch.Unavailable, "queue_state_changed"):
            self.collect()
        self.assertEqual(self.calls[watch.URL], 2)
        self.assertEqual(sum(self.calls.values()), 8)

    def test_runner_assigned_but_still_queued_retracts_warning(self):
        self.confirm_jobs[2].update({
            "runner_id": 21,
            "runner_name": "zcloud-vps-1",
        })
        with self.assertRaisesRegex(watch.Unavailable, "queue_state_changed"):
            self.collect()

    def test_run_inventory_cannot_drift_between_reads(self):
        self.confirm_runs[0]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(watch.Unavailable, "queue_run_inventory_changed"):
            self.collect()
        self.assertEqual(sum(self.calls.values()), 5)

    def test_run_count_must_remain_stable(self):
        self.confirm_total_count = 4
        with self.assertRaisesRegex(watch.Unavailable, "queue_run_inventory_changed"):
            self.collect()

    def test_drift_in_unchanged_queue_metadata_is_not_a_stable_receipt(self):
        self.confirm_jobs[1]["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.assertRaisesRegex(watch.Unavailable, "queue_state_changed"):
            self.collect()

    def test_second_job_inventory_must_be_complete(self):
        self.confirm_job_count[3] = 2
        with self.assertRaisesRegex(watch.Unavailable, "queue_job_inventory_changed"):
            self.collect()

    def test_truncated_sample_does_not_issue_warning_or_extra_requests(self):
        self.total_count = 31
        report = self.collect()
        self.assertEqual(report["classification"], "INCOMPLETE_QUEUE_SAMPLE")
        self.assertEqual(self.calls[watch.URL], 1)
        self.assertEqual(sum(self.calls.values()), 4)

    def test_positive_runner_assignment_does_not_authorize_restart(self):
        self.jobs[1].update({"runner_id": 21, "runner_name": "zcloud-vps-1",
                             "status": "in_progress"})
        self.jobs[1]["created_at"] = datetime.now(timezone.utc).isoformat()
        report = self.collect()
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertFalse(report["runner_offline_verified"])
        self.assertFalse(report["mutation_authorized"])
        self.assertEqual(sum(self.calls.values()), 4)
