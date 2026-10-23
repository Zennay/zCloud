"""Hermetic event chronology regression for GitHub-hosted VPS queue observations.

No external API, runner, browser, queue or SQLite connection is made.
A job belonging to a workflow_run cannot have existed before that run.
"""
from datetime import timedelta
from pathlib import Path
import unittest

from scripts import zcloud_runner_queue_watch_20261014 as watch
from tests.test_zcloud_runner_queue_watch_20261014 import NOW, iso, sample

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-runner-queue-watch-hosted.yml")


def summary(run, job):
    return watch.summarize([run], {run["id"]: [job]}, NOW)


class HostedRunnerQueueChronologyTests(unittest.TestCase):
    def test_matching_run_and_job_timestamps_remain_valid(self):
        run, job = sample(7, age=1500)
        report = summary(run, job)
        self.assertEqual(1, report["stale_unassigned"])
        self.assertEqual("INSUFFICIENT_QUEUE_EVIDENCE", report["classification"])

    def test_job_created_long_before_workflow_run_denied(self):
        for seconds in (6, 60, 300, 3600):
            run, job = sample(7, age=70, status="in_progress", runner=21)
            job["created_at"] = iso(70 + seconds)
            with self.subTest(seconds=seconds), self.assertRaisesRegex(
                watch.Unavailable, "job_before_workflow_run"
            ):
                summary(run, job)

    def test_small_git_timestamp_resolution_tolerance(self):
        for seconds in (1, 3, 5):
            run, job = sample(7, age=70, status="in_progress", runner=21)
            job["created_at"] = iso(70 + seconds)
            with self.subTest(seconds=seconds):
                report = summary(run, job)
                self.assertEqual("RECENT_RUNNER_ASSIGNMENT_OBSERVED",
                                 report["classification"])

    def test_retried_workflow_may_have_much_newer_job(self):
        run, job = sample(7, age=70, status="in_progress", runner=21)
        run["created_at"] = iso(watch.MAX_AGE + 1000)
        run["run_attempt"] = 2
        job["run_attempt"] = 2
        report = summary(run, job)
        self.assertEqual(1, report["recent_assignments"])
        self.assertEqual("RECENT_RUNNER_ASSIGNMENT_OBSERVED",
                         report["classification"])
        self.assertFalse(report["runner_offline_verified"])

    def test_old_job_with_newer_run_denied_even_when_outside_lookback(self):
        run, job = sample(7, age=180)
        job["created_at"] = iso(watch.MAX_AGE + 400)
        with self.assertRaisesRegex(watch.Unavailable, "job_before_workflow_run"):
            summary(run, job)

    def test_timezone_offsets_are_compared_as_actual_instants(self):
        run, job = sample(7, age=180)
        original = NOW - timedelta(seconds=180)
        job["created_at"] = (original - timedelta(seconds=30)).astimezone(
            watch.timezone(timedelta(hours=2))
        ).isoformat()
        with self.assertRaisesRegex(watch.Unavailable, "job_before_workflow_run"):
            summary(run, job)

    def test_invalid_or_missing_job_timestamp_is_denied(self):
        for value in ("", None, "2026-10-14T18:30:00", 1):
            run, job = sample(7, age=180)
            job["created_at"] = value
            with self.subTest(value=value), self.assertRaises(watch.Unavailable):
                summary(run, job)

    def test_source_gate_is_hosted_and_readonly(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("runs-on: [self-hosted", text)
        self.assertIn("tests.test_deployops_runner_queue_chronology_20261014_w8",
                      text)
        self.assertIn("tests/test_deployops_runner_queue_chronology_20261014_w8.py",
                      text)
        self.assertNotIn("sudo systemctl", text)
        self.assertNotIn("gh run cancel", text)


if __name__ == "__main__":
    unittest.main()
