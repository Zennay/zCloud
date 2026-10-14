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
    run = {"id": n, "name": m.NAME, "run_attempt": 1,
           "event": "pull_request", "head_branch": "patch",
           "path": m.PATH + "@refs/pull/1403/merge",
           "head_sha": "a" * 40, "created_at": iso(age),
           "repository": {"full_name": m.REPO},
           "head_repository": {"full_name": m.REPO}}
    job = {"id": 1000 + n, "run_id": n, "name": "cpu-snapshot",
           "workflow_name": m.NAME, "head_branch": run["head_branch"],
           "head_sha": run["head_sha"], "run_attempt": 1,
           "run_url": f"https://api.github.com/repos/{m.REPO}/actions/runs/{n}",
           "url": f"https://api.github.com/repos/{m.REPO}/actions/jobs/{1000+n}",
           "html_url": f"https://github.com/{m.REPO}/actions/runs/{n}/job/{1000+n}",
           "created_at": iso(age), "status": status,
           "runner_id": runner,
           "runner_name": "zcloud-vps-1" if runner else None,
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
        self.assertEqual(report["oldest_queued_minutes"], 25)
        self.assertEqual(report["queued_over_2h"], 0)
        self.assertEqual(report["inspected_recent_runs"], 3)
        self.assertFalse(report["sample_truncated"])
        for key in ("runner_offline_verified", "production_verified",
                    "mutation_authorized", "mutations_performed"):
            self.assertIs(report[key], False)

    def test_fresh_assignment_prevents_false_gap(self):
        report = self.classify(sample(1), sample(2), sample(3),
                               sample(4, age=60, status="in_progress", runner=21))
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertEqual(report["recent_assignments"], 1)
        self.assertFalse(report["production_verified"])

    def test_spoofed_numeric_identity_cannot_prove_runner_recovery(self):
        pair = sample(1, age=60, status="in_progress", runner=21)
        good = self.classify(pair)
        self.assertEqual(good["recent_assignments"], 1)
        for data in ({"runner_id": 999, "runner_name": "zcloud-vps-1"},
                     {"runner_id": 21, "runner_name": "other"},
                     {"runner_id": 21, "runner_name": None},
                     {"status": "completed", "conclusion": None}):
            run, job = sample(1, age=60, status="in_progress", runner=21)
            job.update(data)
            with self.subTest(data=data), self.assertRaises(m.Unavailable):
                self.classify((run, job))

    def test_oldest_queue_and_failed_assignments_are_separate(self):
        report = self.classify(sample(1, age=8000), sample(2, age=7300),
                               sample(3, age=2400),
                               sample(4, age=60, status="completed", runner=21))
        self.assertEqual(report["queued_over_2h"], 2)
        self.assertEqual(report["oldest_queued_minutes"], 133)
        self.assertEqual(report["classification"], "RECENT_RUNNER_ASSIGNMENT_OBSERVED")
        self.assertEqual(report["assigned_failures"], 0)
        r, j = sample(6, age=40, status="completed", runner=21)
        j["conclusion"] = "failure"
        self.assertEqual(self.classify((r, j))["assigned_failures"], 1)

    def test_partial_window_is_disclosed_not_claimed_exhaustive(self):
        r, j = sample(1)
        x = m.summarize([r], {1: [j]}, NOW, total_count=275)
        self.assertTrue(x["sample_truncated"])
        self.assertEqual(x["total_workflow_runs"], 275)
        self.assertEqual(x["sampled_runs"], 1)
        for invalid in (-1, 0, True, "275"):
            with self.subTest(invalid=invalid), self.assertRaises(m.Unavailable):
                m.summarize([r], {1: [j]}, NOW, total_count=invalid)

    def test_queued_job_identity_must_match_exact_run(self):
        for mutation in ({"workflow_name": "Other workflow"},
                         {"head_branch": "elsewhere"},
                         {"head_sha": "b"*40}, {"run_attempt": 2},
                         {"run_attempt": True}, {"run_url": "https://attacker.invalid"},
                         {"url": "https://attacker.invalid"},
                         {"html_url": "https://attacker.invalid"},
                         {"id": True}, {"id": 9223372036854775808}):
            r, j = sample(1)
            j.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(m.Unavailable, "unbound_job_provenance"):
                self.classify((r, j))

    def test_not_enough_jobs_or_new_jobs_are_inconclusive(self):
        for pairs in ((sample(1),), (sample(1, 1500), sample(2, 60)),
                      (sample(1, 20), sample(2, 40), sample(3, 50))):
            self.assertEqual(self.classify(*pairs)["classification"],
                             "INSUFFICIENT_QUEUE_EVIDENCE")

    def test_unknown_labels_runner_or_job_status_are_not_outage_proof(self):
        for patch in ({"labels": ["self-hosted", "zcloud", "vps", "private"]},
                      {"labels": ["self-hosted", "zcloud", "vps", "VPS"]},
                      {"labels": None}, {"runner_id": True},
                      {"runner_id": 21}, {"runner_name": "spoofed-runner"},
                      {"status": "requested"},
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

    def test_real_http_parser_is_bounded_to_exact_workflow_and_jobs(self):
        from unittest.mock import patch

        runs, jobs = [], {}
        for number in (1, 2, 3):
            run, job = sample(number, age=1800)
            runs.append(run)
            jobs[number] = job

        class Headers:
            def get_content_type(self):
                return "application/json"

        class Reply:
            headers = Headers()
            def __init__(self, payload):
                self.payload = json.dumps(payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                assert n == 1024 * 1024 + 1
                return self.payload[:n]

        class Opener:
            def __init__(self):
                self.urls = []
            def open(self, request, timeout):
                self.urls.append(request.full_url)
                self_timeout = timeout
                assert self_timeout == 12
                assert request.get_header("Authorization") == "Bearer test-token"
                if request.full_url == m.URL:
                    return Reply({"total_count": 99, "workflow_runs": runs})
                for rid, job in jobs.items():
                    if request.full_url.endswith(f"/actions/runs/{rid}/jobs?per_page=100"):
                        return Reply({"total_count": 1, "jobs": [job]})
                raise AssertionError("unexpected GitHub request")

        opener = Opener()
        result = m.collect("test-token", opener=opener)
        self.assertEqual(result["sampled_runs"], 3)
        self.assertTrue(result["sample_truncated"])
        self.assertEqual(result["total_workflow_runs"], 99)
        self.assertEqual(len(opener.urls), 4)
        self.assertFalse(result["production_verified"])
        self.assertFalse(result["mutation_authorized"])
        self.assertTrue(all("api.github.com/repos/Zennay/zCloud/" in url
                            for url in opener.urls))

        class MissingJobOpener(Opener):
            def open(self, request, timeout):
                if request.full_url.endswith("/actions/runs/2/jobs?per_page=100"):
                    return Reply({"total_count": 2, "jobs": [jobs[2]]})
                return super().open(request, timeout)
        with self.assertRaisesRegex(m.Unavailable, "incomplete_job_page"):
            m.collect("test-token", opener=MissingJobOpener())

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
        for field in ("queued_over_2h", "oldest_queued_minutes",
                      "sample_truncated", "total_workflow_runs",
                      "assigned_failures"):
            self.assertIn(field, wf)


if __name__ == "__main__":
    unittest.main()
