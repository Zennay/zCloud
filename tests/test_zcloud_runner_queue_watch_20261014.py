"""Offline regression for unattended self-hosted queue watch (no runner, network or writes)."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from scripts import zcloud_runner_queue_watch_20261014 as m

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-runner-queue-watch-hosted.yml"
NOW = datetime(2026, 10, 14, 10, 40, tzinfo=timezone.utc)


def iso(seconds):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def sample(n, age=1500, *, status="queued", runner=0):
    run = {"id": n, "name": m.NAME,
           "event": "pull_request", "head_branch": "patch",
           "path": m.PATH + "@refs/pull/1403/merge",
           "head_sha": "a" * 40, "created_at": iso(age),
           "repository": {"full_name": m.REPO},
           "head_repository": {"full_name": m.REPO}}
    job = {"id": 1000 + n, "run_id": n, "name": "cpu-snapshot",
           "created_at": iso(age), "status": status,
           "runner_id": runner,
           "labels": ["self-hosted", "zcloud", "vps"],
           "conclusion": None}
    if status == "completed":
        job["conclusion"] = "success"
    return run, job


class QueueWatchTests(unittest.TestCase):
    def classify(self, *pairs):
        return m.summarize([r for r, _ in pairs],
                           {r["id"]: [j] for r, j in pairs}, NOW)

    def test_three_old_distinct_unassigned_jobs_produce_cautious_warning(self):
        report = self.classify(sample(1), sample(2, 1400), sample(3, 1300))
        self.assertEqual(report["classification"], "POSSIBLE_LISTENER_GAP")
        self.assertEqual(report["stale_unassigned"], 3)
        for key in ("runner_offline_verified", "production_verified",
                    "mutation_authorized", "mutations_performed"):
            self.assertIs(report[key], False)

    def test_fresh_assignment_prevents_false_gap(self):
        report = self.classify(sample(1), sample(2), sample(3),
                               sample(4, age=60, status="in_progress", runner=21))
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertEqual(report["recent_assignments"], 1)
        self.assertFalse(report["production_verified"])

    def test_not_enough_jobs_or_new_jobs_are_inconclusive(self):
        for pairs in ((sample(1),), (sample(1, 1500), sample(2, 60)),
                      (sample(1, 20), sample(2, 40), sample(3, 50))):
            self.assertEqual(self.classify(*pairs)["classification"],
                             "INSUFFICIENT_QUEUE_EVIDENCE")

    def test_unknown_labels_runner_or_job_status_are_not_outage_proof(self):
        for patch in ({"labels": ["self-hosted", "zcloud", "vps", "private"]},
                      {"labels": ["self-hosted", "zcloud", "vps", "VPS"]},
                      {"labels": None}, {"runner_id": True},
                      {"runner_id": 21}, {"status": "requested"},
                      {"conclusion": "success"}, {"created_at": iso(-60)}):
            r, j = sample(1)
            j.update(patch)
            with self.subTest(patch=patch), self.assertRaises(m.Unavailable):
                self.classify((r, j))

    def test_wrong_workflow_repo_and_event_are_denied(self):
        for patch in ({"name": "attacker-run"}, {"path": ".github/workflows/other.yml"},
                      {"event": "workflow_run"}, {"head_sha": "x" * 40},
                      {"repository": {"full_name": "someone/zCloud"}},
                      {"head_repository": {"full_name": "another/repo"}},
                      {"created_at": iso(-100)}):
            r, j = sample(1)
            r.update(patch)
            with self.subTest(patch=patch), self.assertRaises(m.Unavailable):
                self.classify((r, j))

    def test_duplicate_run_missing_jobs_and_mismatched_job_identity_denied(self):
        r, j = sample(1)
        with self.assertRaises(m.Unavailable):
            m.summarize([r, r], {1: [j]}, NOW)
        with self.assertRaises(m.Unavailable):
            m.summarize([r], {}, NOW)
        with self.assertRaises(m.Unavailable):
            m.summarize([r], {1: [dict(j, run_id=2)]}, NOW)

    def test_no_schema_confusion_or_sensitive_api_leak(self):
        for raw in (b'{"x":1,"x":2}', b'[]', b'{"x":NaN}',
                    b'{"x":1' , b'X' * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(m.Unavailable):
                m.strict_json(raw)
        x = self.classify(sample(1))
        self.assertNotIn("job_id", json.dumps(x))
        self.assertNotIn("runner_name", json.dumps(x))
        self.assertNotIn("token", json.dumps(x))
        self.assertEqual(m.NAME, "VPS CPU diagnostic snapshot")

    def test_tokens_and_redirects_are_bounded(self):
        for token in ("", "foo bar", "x"*2049, "token\nheader", "aaa/bbb"):
            with self.subTest(n=len(token)), self.assertRaises(m.Unavailable):
                m.collect(token, opener=object())
        with self.assertRaises(m.Unavailable):
            m.NoRedirect().redirect_request(None, None, 302, "", {}, "http://attacker/")

    def test_workflow_hosted_readonly_on_all_events(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  schedule:", wf)
        self.assertIn("  pull_request:", wf)
        self.assertIn("  workflow_dispatch:", wf)
        for forbidden in ("\n  push:", "\n  workflow_run:", "runs-on: self-hosted",
                          "actions: write", "contents: write", "systemctl restart",
                          "sudo ", "gh workflow run", "git push"):
            self.assertNotIn(forbidden, wf)
        self.assertIn("  actions: read", wf)
        self.assertIn("  contents: read", wf)
        self.assertEqual(wf.count("runs-on: ubuntu-latest"), 2)
        self.assertIn("github.triggering_actor == 'Zennay'", wf)
        self.assertIn("github.run_attempt == 1", wf)
        self.assertIn("retention-days: 7", wf)


if __name__ == "__main__":
    unittest.main()
