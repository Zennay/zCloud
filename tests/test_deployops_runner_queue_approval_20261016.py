"""Offline fail-closed classification for approval-blocked Actions run queues.

An awaiting-approval or pre-start workflow is not an unassigned runner job.
No GitHub API, VPS, browser, SQLite or production process is accessed.
"""
from copy import deepcopy
from pathlib import Path
import unittest

from scripts import zcloud_runner_queue_watch_20261014 as watch
from tests.test_zcloud_runner_queue_watch_20261014 import NOW, sample

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/zcloud-runner-queue-watch-hosted.yml"


def summary(*pairs):
    return watch.summarize([run for run, _ in pairs],
                           {run["id"]: [job] for run, job in pairs}, NOW)


def runstate(rid, state, age=1800):
    run, job = sample(rid, age=age)
    run["status"] = state
    return run, job


class ApprovalQueueTrustTests(unittest.TestCase):
    def test_workflow_approval_or_prestart_does_not_prove_listener_gap(self):
        for state in ("waiting", "pending", "requested"):
            with self.subTest(state=state):
                report = summary(runstate(1, state), runstate(2, state), runstate(3, state))
                self.assertEqual(report["classification"], "INSUFFICIENT_QUEUE_EVIDENCE")
                self.assertEqual(report["approval_or_prestart"], 3)
                self.assertEqual(report["stale_unassigned"], 0)
                self.assertEqual(report["queue_jobs_observed"], 0)
                self.assertIs(report["runner_offline_verified"], False)
                self.assertIs(report["mutation_authorized"], False)

    def test_mixed_approval_with_two_genuinely_queued_jobs_stays_insufficient(self):
        report = summary(runstate(1, "waiting"), runstate(2, "queued"), runstate(3, "in_progress"))
        self.assertEqual(report["stale_unassigned"], 2)
        self.assertEqual(report["approval_or_prestart"], 1)
        self.assertEqual(report["classification"], "INSUFFICIENT_QUEUE_EVIDENCE")

    def test_three_genuinely_queued_run_states_remain_a_cautious_signal(self):
        report = summary(runstate(1, "queued"), runstate(2, "in_progress"),
                         runstate(3, "queued"))
        self.assertEqual(report["stale_unassigned"], 3)
        self.assertEqual(report["approval_or_prestart"], 0)
        self.assertEqual(report["classification"], "POSSIBLE_LISTENER_GAP")
        self.assertIs(report["runner_offline_verified"], False)

    def test_recent_assignment_wins_when_some_workflows_await_approval(self):
        approved = runstate(1, "waiting")
        run, job = sample(2, age=30, runner=21, status="in_progress")
        run["status"] = "in_progress"
        report = summary(approved, (run, job))
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertEqual(report["approval_or_prestart"], 1)
        self.assertEqual(report["recent_assignments"], 1)
        self.assertIs(report["production_verified"], False)

    def test_a_completed_run_cannot_still_have_queued_cpu_job(self):
        run, job = runstate(1, "completed")
        run["conclusion"] = "success"
        with self.assertRaisesRegex(watch.Unavailable, "terminal_run_has_unfinished_job"):
            summary((run, job))

    def test_waiting_workflow_with_assigned_or_running_job_is_contradictory(self):
        for state in ("waiting", "pending", "requested"):
            for status, runner_id in (("queued", 21), ("in_progress", 21),
                                      ("completed", 21)):
                run, job = sample(1, age=1300, status=status, runner=runner_id)
                run["status"] = state
                if status == "completed":
                    job["conclusion"] = "success"
                with self.subTest(state=state, status=status), self.assertRaisesRegex(
                        watch.Unavailable, "non_schedulable_run_job_contradiction"):
                    summary((run, job))

    def test_nonterminal_workflow_conclusion_fails_closed(self):
        for state in ("queued", "in_progress", "waiting", "requested", "pending"):
            run, job = runstate(1, state)
            run["conclusion"] = "success"
            with self.subTest(state=state), self.assertRaisesRegex(
                    watch.Unavailable, "nonterminal_workflow_run_conclusion"):
                summary((run, job))

    def test_unknown_run_state_does_not_authorize_a_negative_listener_claim(self):
        for value in ("unknown", "waiting_approval", 2, True, [], {}):
            run, job = runstate(1, value)
            with self.subTest(value=repr(value)), self.assertRaisesRegex(
                    watch.Unavailable, "unknown_workflow_run_status"):
                summary((run, job))

    def test_completed_workflow_conclusion_must_be_terminal(self):
        for value in ("", None, "in_progress", True, 4):
            run, job = sample(1, status="completed", runner=21)
            run["status"] = "completed"
            run["conclusion"] = value
            with self.subTest(value=repr(value)), self.assertRaisesRegex(
                    watch.Unavailable, "unknown_workflow_run_conclusion"):
                summary((run, job))

    def test_complete_run_and_completed_assigned_job_remains_valid(self):
        run, job = sample(1, age=30, runner=21, status="completed")
        run["status"] = "completed"
        run["conclusion"] = "success"
        report = summary((run, job))
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertEqual(report["assigned_failures"], 0)
        self.assertEqual(report["approval_or_prestart"], 0)

    def test_hosted_only_ci_exact_head_and_count_only_reporting(self):
        workflow = WF.read_text()
        self.assertIn("tests.test_deployops_runner_queue_approval_20261016", workflow)
        self.assertIn("tests/test_deployops_runner_queue_approval_20261016.py", workflow)
        self.assertIn("Approval/prestart (not runner evidence)", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("runs-on: [self-hosted", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$SOURCE_SHA"', workflow)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', workflow)
        self.assertIn("runner_offline_verified", (ROOT / "scripts/zcloud_runner_queue_watch_20261014.py").read_text())


if __name__ == "__main__":
    unittest.main()
