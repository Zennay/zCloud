"""Offline test suite for a source-only VPS diagnostic queue auditor."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

BASE_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("queue_storm", BASE_DIR / "scripts/zcloud_cpu_queue_storm_audit_20261016.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
NOW = datetime(2026, 10, 16, 15, 46, tzinfo=timezone.utc)


def make_run(index, *, sha="a" * 40, branch="one", age=1900, status="queued", event="pull_request"):
    return {"id": index, "repository": {"full_name": audit.REPO},
            "head_repository": {"full_name": audit.REPO}, "name": audit.WORKFLOW_NAME,
            "path": audit.WORKFLOW_PATH + "@refs/heads/" + branch,
            "head_branch": branch, "head_sha": sha, "run_attempt": 1,
            "event": event, "status": status, "conclusion": None,
            "created_at": (NOW - timedelta(seconds=age+3)).isoformat()}


def make_job(run, *, age=1800, status="queued", runner_id=0):
    return {"id": run["id"] + 100000, "run_id": run["id"], "run_attempt": 1,
            "name": "cpu-snapshot", "workflow_name": audit.WORKFLOW_NAME,
            "head_branch": run["head_branch"], "head_sha": run["head_sha"],
            "labels": ["self-hosted", "zcloud", "vps"], "status": status,
            "conclusion": None, "runner_id": runner_id,
            "runner_name": "zcloud-vps-1" if runner_id else "",
            "created_at": (NOW - timedelta(seconds=age)).isoformat(),
            "run_url": f"{audit.BASE}/actions/runs/{run['id']}",
            "url": f"{audit.BASE}/actions/jobs/{run['id'] + 100000}",
            "html_url": f"https://github.com/{audit.REPO}/actions/runs/{run['id']}/job/{run['id'] + 100000}"}


def payloads(runs):
    return {"total_count": len(runs), "workflow_runs": runs}, {
        r["id"]: {"total_count": 1, "jobs": [make_job(r)]} for r in runs}


class QueueStormContract(unittest.TestCase):
    def evaluate(self, *runs, cap=20):
        p, j = payloads(list(runs))
        return audit.receipt(audit.read_sample(p, j, NOW, cap), stable=True)

    def test_identical_sha_repeated_diagnostics_are_review_only(self):
        r = self.evaluate(make_run(1), make_run(2), make_run(3))
        self.assertEqual(r["sampled_unassigned_over_15m"], 3)
        self.assertEqual(r["repeated_sha_groups"][0]["sample_count"], 3)
        self.assertEqual(r["repeated_sha_groups"][0]["run_ids"], [1, 2, 3])
        self.assertFalse(r["cancel_authorized"])
        self.assertFalse(r["deploy_authorized"])
        self.assertFalse(r["runner_offline_verified"])
        self.assertFalse(r["mutation_performed"])

    def test_different_sha_or_branch_or_event_not_collapsed(self):
        r = self.evaluate(make_run(1), make_run(2, sha="b"*40),
                          make_run(3, branch="other"), make_run(4, event="workflow_dispatch"))
        self.assertEqual(r["repeated_sha_groups"], [])

    def test_zero_sample_is_complete_if_github_reports_zero(self):
        r = self.evaluate()
        self.assertTrue(r["complete"])
        self.assertEqual(r["sample_count"], 0)

    def test_under_threshold_does_not_count_as_stale(self):
        p, j = payloads([make_run(1, age=600)])
        j[1]["jobs"][0]["created_at"] = (NOW - timedelta(seconds=500)).isoformat()
        s = audit.read_sample(p, j, NOW, 20)
        self.assertEqual(audit.receipt(s, stable=True)["sampled_unassigned_over_15m"], 0)

    def test_uncertain_parent_approval_is_not_runner_gap(self):
        for state in ("waiting", "pending", "requested"):
            with self.subTest(state=state):
                r = self.evaluate(make_run(1, status=state))
                self.assertEqual(r["sampled_approval_or_prestart"], 1)
                self.assertEqual(r["sampled_unassigned"], 0)

    def test_runner_assigned_does_not_count_as_unassigned(self):
        p, j = payloads([make_run(1)])
        j[1]["jobs"][0].update({"runner_id": 21, "runner_name": "zcloud-vps-1"})
        self.assertEqual(audit.receipt(audit.read_sample(p, j, NOW, 20), stable=True)["sampled_assigned"], 1)

    def test_missing_parent_run_status_fails_closed(self):
        for state in (None, "", "in_progress", 7, False):
            p, j = payloads([make_run(1)])
            p["workflow_runs"][0]["status"] = state
            with self.subTest(state=state), self.assertRaises(audit.Unavailable):
                audit.read_sample(p, j, NOW, 20)
        p, j = payloads([make_run(1)])
        del p["workflow_runs"][0]["status"]
        with self.assertRaises(audit.Unavailable):
            audit.read_sample(p, j, NOW, 20)

    def test_mismatched_repo_and_workflow_denied(self):
        for field, value in (("name", "hosted"), ("path", "other.yml"),
                             ("event", "schedule"), ("head_sha", "not-a-sha")):
            p, j = payloads([make_run(1)])
            p["workflow_runs"][0][field] = value
            with self.subTest(field=field), self.assertRaises(audit.Unavailable):
                audit.read_sample(p, j, NOW, 20)
        p, j = payloads([make_run(1)])
        p["workflow_runs"][0]["head_repository"]["full_name"] = "attacker/repo"
        with self.assertRaises(audit.Unavailable):
            audit.read_sample(p, j, NOW, 20)

    def test_missing_or_ambiguous_job_page_denied(self):
        p, j = payloads([make_run(1)])
        for invalid in ({"total_count": 0, "jobs": []},
                        {"total_count": 2, "jobs": j[1]["jobs"]*2}, None):
            with self.subTest(invalid=invalid), self.assertRaises(audit.Unavailable):
                audit.read_sample(p, {1: invalid}, NOW, 20)

    def test_job_urls_must_bind_same_run_and_job_identity(self):
        for field, value in (("run_url", f"{audit.BASE}/actions/runs/2"),
                             ("url", f"{audit.BASE}/actions/jobs/1"),
                             ("html_url", "https://github.com/Zennay/zCloud/actions/runs/1/job/999"),
                             ("html_url", None)):
            p, j = payloads([make_run(1)])
            j[1]["jobs"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    audit.Unavailable, "job_url_binding_invalid"):
                audit.read_sample(p, j, NOW, 20)

    def test_runner_prestart_and_executing_contradictions_fail_closed(self):
        for run_state in ("waiting", "pending", "requested"):
            for job_state, runner_id in (("in_progress", 21),
                                         ("queued", 21), ("in_progress", 0)):
                with self.subTest(run=run_state, job=job_state, runner=runner_id):
                    p, j = payloads([make_run(1, status=run_state)])
                    job = j[1]["jobs"][0]
                    job["status"] = job_state
                    job["runner_id"] = runner_id
                    job["runner_name"] = "zcloud-vps-1" if runner_id else ""
                    with self.assertRaises(audit.Unavailable):
                        audit.read_sample(p, j, NOW, 20)
        p, j = payloads([make_run(1)])
        j[1]["jobs"][0]["status"] = "in_progress"
        with self.assertRaisesRegex(audit.Unavailable, "unassigned_executing_job"):
            audit.read_sample(p, j, NOW, 20)

    def test_boolean_identifiers_and_page_counts_cannot_spoof_one(self):
        for field, value in (("run_id", True), ("run_attempt", True)):
            p, j = payloads([make_run(1)])
            j[1]["jobs"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    audit.Unavailable, "job_run_binding_invalid"):
                audit.read_sample(p, j, NOW, 20)
        p, j = payloads([make_run(1)])
        j[1]["total_count"] = True
        with self.assertRaisesRegex(audit.Unavailable, "job_page_incomplete"):
            audit.read_sample(p, j, NOW, 20)

    def test_cross_pr_queue_pressure_is_bounded_and_not_a_listener_claim(self):
        result = self.evaluate(make_run(1, sha="a"*40),
                               make_run(2, sha="b"*40),
                               make_run(3, sha="c"*40, event="workflow_dispatch"))
        self.assertEqual(result["repeated_sha_groups"], [])
        self.assertEqual(result["sampled_unique_unassigned_heads"], 3)
        self.assertEqual(result["sampled_unassigned_by_event"],
                         {"pull_request": 2, "workflow_dispatch": 1})
        self.assertEqual(result["oldest_unassigned_job_minutes"], 30)
        self.assertFalse(result["runner_offline_verified"])
        self.assertFalse(result["cancel_authorized"])
        self.assertFalse(result["deploy_authorized"])

    def test_approval_and_assigned_jobs_not_counted_as_queue_pressure(self):
        p, j = payloads([make_run(1, status="waiting"), make_run(2)])
        j[2]["jobs"][0].update({"runner_id": 21, "runner_name": "zcloud-vps-1"})
        result = audit.receipt(audit.read_sample(p, j, NOW, 20), stable=True)
        self.assertEqual(result["sampled_unique_unassigned_heads"], 0)
        self.assertIsNone(result["oldest_unassigned_job_minutes"])
        self.assertEqual(result["sampled_unassigned_by_event"],
                         {"pull_request": 0, "workflow_dispatch": 0})

    def test_contradictory_runner_fields_and_labels_denied(self):
        for key, value in (("runner_id", True), ("runner_name", "fake"),
                           ("labels", ["self-hosted", "zcloud"]),
                           ("status", "completed"), ("conclusion", "success")):
            p, j = payloads([make_run(1)])
            j[1]["jobs"][0][key] = value
            with self.subTest(key=key), self.assertRaises(audit.Unavailable):
                audit.read_sample(p, j, NOW, 20)

    def test_incomplete_window_disclosed_not_full_claim(self):
        p, j = payloads([make_run(1), make_run(2)])
        p["total_count"] = 7
        q = audit.receipt(audit.read_sample(p, j, NOW, 2), stable=True)
        self.assertFalse(q["complete"])
        self.assertEqual(q["total_queued_workflow_runs"], 7)
        self.assertEqual(q["sample_count"], 2)

    def test_incomplete_under_reported_page_denied(self):
        p, j = payloads([make_run(1), make_run(2)])
        p["total_count"] = 3
        with self.assertRaises(audit.Unavailable):
            audit.read_sample(p, j, NOW, 20)

    def test_mutating_github_observation_never_issues_cancel(self):
        p, j = payloads([make_run(1), make_run(2)])
        first = True
        url_runs = f"{audit.BASE}/actions/workflows/{audit.WORKFLOW}/runs?status=queued&per_page=20&page=1"
        def getter(url):
            nonlocal first
            if url == url_runs:
                if first:
                    first = False
                    return p
                changed = dict(p)
                changed["total_count"] = 3
                return changed
            if url.endswith("/jobs?per_page=100"):
                rid = int(url.split("/")[-2])
                return j[rid]
            raise AssertionError(url)
        with self.assertRaises(audit.Unavailable):
            audit.collect("offline-token", getter=getter, now=NOW)

    def test_stable_two_pass_no_write_and_changed_clears_groups(self):
        p, j = payloads([make_run(1), make_run(2)])
        url_runs = f"{audit.BASE}/actions/workflows/{audit.WORKFLOW}/runs?status=queued&per_page=20&page=1"
        def getter(url):
            if url == url_runs:
                return p
            if "/jobs?per_page=100" in url:
                return j[int(url.split("/")[-2])]
            raise AssertionError("unexpected endpoint")
        q = audit.collect("offline-token", getter=getter, now=NOW)
        self.assertTrue(q["complete"])
        self.assertEqual(len(q["repeated_sha_groups"]), 1)
        call_count = 0
        def drifting(url):
            nonlocal call_count
            if url == url_runs:
                call_count += 1
                return p
            return {"total_count": 1, "jobs": [dict(j[int(url.split("/")[-2])]["jobs"][0],
                                                     created_at=(NOW-timedelta(seconds=1800+call_count)).isoformat())]}
        q = audit.collect("offline-token", getter=drifting, now=NOW)
        self.assertEqual(q["status"], "EVIDENCE_CHANGED")
        self.assertEqual(q["repeated_sha_groups"], [])
        self.assertFalse(q["complete"])

    def test_nonfinite_exponent_json_fails_closed(self):
        for raw in (b'{"bad":1e99999}', b'{"bad":-1e99999}',
                    b'{"nested":[0.1,1e99999]}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                    audit.Unavailable, "nonfinite_json"):
                audit.strict_json(raw)
        self.assertEqual(audit.strict_json(b'{"ok":2.5}')["ok"], 2.5)

    def test_decoded_unicode_surrogates_are_never_accepted(self):
        for raw in (b'{"bad":"\\ud800"}', b'{"\\udfff":1}',
                    b'{"nested":[{"bad":"\\udc00"}]}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                    audit.Unavailable, "surrogate_json"):
                audit.strict_json(raw)
        self.assertEqual(audit.strict_json('{"ok":"✅"}'.encode("utf-8"))["ok"], "✅")

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaisesRegex(audit.Unavailable, "duplicate_json_key"):
            audit.strict_json(b'{"a": 1, "a": 2}')
        with self.assertRaisesRegex(audit.Unavailable, "nonfinite_json"):
            audit.strict_json(b'{"bad":NaN}')
        with self.assertRaises(audit.Unavailable):
            audit.strict_json(b"[]")

    def test_no_mutation_api_paths_and_no_vps_runner_in_gate(self):
        workflow = (BASE_DIR / ".github/workflows/zcloud-cpu-queue-storm-readonly-20261016.yml").read_text()
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("actions: write", workflow)
        source = (BASE_DIR / "scripts/zcloud_cpu_queue_storm_audit_20261016.py").read_text()
        self.assertNotIn('Request(url, data=', source)
        self.assertNotIn('method="POST"', source)
        self.assertNotIn('method="DELETE"', source)


if __name__ == "__main__":
    unittest.main()
