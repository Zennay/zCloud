"""Offline boundary tests for the GET-only queue pagination collector."""
from datetime import datetime, timezone
import json
import unittest

from scripts import zcloud_watchdog_queue_collector_20261014_w1 as collection
from scripts import zcloud_watchdog_queue_admission_20261014_w1 as policy

SHA = "a" * 40
NOW = datetime(2026, 10, 14, 3, tzinfo=timezone.utc)
ROOT = "/repos/Zennay/zCloud"
MAIN = ROOT + "/branches/main"
RUNS = ROOT + "/actions/runs?status=queued&per_page=100&page=1"


def main(sha=SHA):
    return {"commit": {"sha": sha}}, ""


def run(ident):
    return {"id": ident, "head_repository": {"full_name": "Zennay/zCloud"},
            "workflow_id": 30, "run_attempt": 1,
            "head_sha": SHA, "head_branch": "main",
            "event": "push", "status": "queued"}


def job(ident, *, parent=None):
    return {"id": ident, "run_id": ident - 1000 if parent is None else parent,
            "run_attempt": 1, "head_sha": SHA, "head_branch": "main",
            "status": "queued", "labels": ["self-hosted", "zcloud", "vps"]}


def api_snapshot(*runs):
    calls = []
    run_ids = [r["id"] for r in runs]
    def fetch(path):
        calls.append(path)
        if path == MAIN:
            return main()
        if path == RUNS:
            return {"total_count": len(runs), "workflow_runs": list(runs)}, ""
        for ident in run_ids:
            if path == ROOT + f"/actions/runs/{ident}/jobs?per_page=100&page=1":
                return {"total_count": 1, "jobs": [job(1000 + ident)]}, ""
        raise AssertionError(f"unexpected GET {path}")
    return fetch, calls


class TestReadOnlyQueueCollector(unittest.TestCase):
    def test_empty_queue_persists_no_mutation(self):
        get, calls = api_snapshot()
        evidence = collection.collect(get, clock=NOW)
        self.assertEqual(evidence["run_pages"][0]["runs"], [])
        self.assertEqual(calls.count(MAIN), 2)
        decision = policy.assess(evidence, expected_main_sha=SHA, now=NOW)
        self.assertEqual(decision["decision"], "NOOP")
        self.assertFalse(decision["inventory_complete"])
        self.assertFalse(decision["mutation_authorized"])

    def test_two_runs_are_review_only_after_real_source_collection(self):
        get, _ = api_snapshot(run(5), run(9))
        evidence = collection.collect(get, clock=NOW)
        result = policy.assess(evidence, expected_main_sha=SHA, now=NOW)
        self.assertEqual(result["candidate_duplicate_groups"], [[5, 9]])
        self.assertEqual(result["cancel_run_ids"], [])
        self.assertEqual(result["decision"], "REVIEW_ONLY")

    def test_page_two_is_fully_traversed(self):
        rows = [run(i) for i in range(1, 102)]
        first = ROOT + "/actions/runs?status=queued&per_page=100&page=1"
        second = ROOT + "/actions/runs?status=queued&per_page=100&page=2"
        def fetch(path):
            if path == MAIN:
                return main()
            if path == first:
                return {"total_count": 101, "workflow_runs": rows[:100]}, (
                    '<https://api.github.com' + second + '>; rel="next"'
                )
            if path == second:
                return {"total_count": 101, "workflow_runs": rows[100:]}, ""
            if path.endswith("/jobs?per_page=100&page=1"):
                ident = int(path.split("/runs/")[1].split("/")[0])
                return {"total_count": 1, "jobs": [job(1000 + ident)]}, ""
            raise AssertionError(path)
        snapshot = collection.collect(fetch, clock=NOW)
        self.assertEqual([len(page["runs"]) for page in snapshot["run_pages"]], [100, 1])
        result = policy.assess(snapshot, expected_main_sha=SHA, now=NOW)
        self.assertEqual(result["decision"], "REVIEW_ONLY")
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(result["cancel_run_ids"], [])


    def test_second_page_swap_with_identical_first_page_is_denied(self):
        """Earlier first-page-only reread missed this queued run replacement."""
        rows = [run(i) for i in range(1, 102)]
        first_seen = [0]
        second_seen = [0]
        second = ROOT + "/actions/runs?status=queued&per_page=100&page=2"
        next_link = '<https://api.github.com' + second + '>; rel="next"'

        def fetch(path):
            if path == MAIN:
                return main()
            if path == RUNS:
                first_seen[0] += 1
                return {"total_count": 101, "workflow_runs": rows[:100]}, next_link
            if path == second:
                second_seen[0] += 1
                last = run(999) if second_seen[0] == 2 else rows[100]
                return {"total_count": 101, "workflow_runs": [last]}, ""
            if path.endswith("/jobs?per_page=100&page=1"):
                ident = int(path.split("/runs/")[1].split("/")[0])
                return {"total_count": 1, "jobs": [job(ident + 1000)]}, ""
            raise AssertionError(path)

        with self.assertRaisesRegex(collection.InventoryError, "queue_changed"):
            collection.collect(fetch, clock=NOW)
        self.assertEqual(second_seen[0], 2)
        self.assertEqual(first_seen[0], 2)

    def test_same_ids_with_changed_workflow_intent_denied(self):
        get, _ = api_snapshot(run(6))
        count = [0]
        def fetch(path):
            if path == RUNS:
                count[0] += 1
                altered = run(6)
                if count[0] == 2:
                    altered["workflow_id"] = 77
                return {"total_count": 1, "workflow_runs": [altered]}, ""
            return get(path)

        with self.assertRaisesRegex(collection.InventoryError, "queue_changed"):
            collection.collect(fetch, clock=NOW)

    def test_slow_snapshot_denied_without_refreshing_its_timestamp(self):
        get, _ = api_snapshot(run(1))
        ticks = iter((20.0, 65.001))
        with self.assertRaisesRegex(collection.InventoryError, "snapshot_acquisition_too_slow"):
            collection.collect(get, clock=NOW, monotonic_clock=lambda: next(ticks))

    def test_clock_reversal_denied(self):
        get, _ = api_snapshot()
        ticks = iter((30.0, 29.99))
        with self.assertRaisesRegex(collection.InventoryError, "snapshot_acquisition_too_slow"):
            collection.collect(get, clock=NOW, monotonic_clock=lambda: next(ticks))

    def test_snapshot_uses_start_instant_not_end_instant(self):
        get, _ = api_snapshot(run(1))
        ticks = iter((10.0, 45.0))
        snapshot = collection.collect(get, clock=NOW, monotonic_clock=lambda: next(ticks))
        self.assertEqual(snapshot["observed_at"], "2026-10-14T03:00:00Z")
        self.assertEqual(
            policy.assess(snapshot, expected_main_sha=SHA, now=NOW)["decision"],
            "NOOP",
        )

    def test_non_utc_start_time_denied_before_http(self):
        from datetime import timedelta
        get, calls = api_snapshot()
        wrong_clock = datetime(2026, 10, 14, 3, tzinfo=timezone(timedelta(hours=2)))
        with self.assertRaisesRegex(collection.InventoryError, "invalid_observation_clock"):
            collection.collect(get, clock=wrong_clock)
        self.assertEqual(calls, [])


    def test_second_pass_run_boolean_attempt_cannot_masquerade_as_one(self):
        get, _ = api_snapshot(run(1))
        seen = [0]
        def forged(path):
            if path == RUNS:
                seen[0] += 1
                altered = run(1)
                if seen[0] == 2:
                    altered["run_attempt"] = True  # True == 1 in Python
                return {"total_count": 1, "workflow_runs": [altered]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "invalid_run_identity"):
            collection.collect(forged, clock=NOW)

    def test_second_pass_run_boolean_workflow_id_must_deny(self):
        get, _ = api_snapshot(run(1))
        seen = [0]
        def forged(path):
            if path == RUNS:
                seen[0] += 1
                altered = run(1)
                if seen[0] == 2:
                    altered["workflow_id"] = True
                return {"total_count": 1, "workflow_runs": [altered]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "invalid_run_identity"):
            collection.collect(forged, clock=NOW)

    def test_second_pass_candidate_job_boolean_ids_must_deny(self):
        for name, replacement in (("run_id", True), ("run_attempt", True),
                                  ("runner_id", True)):
            with self.subTest(field=name):
                # Single-run observations are not candidates: give two
                # same-intent runs so the second job GET is mandatory.
                get, _ = api_snapshot(run(1), run(2))
                seen = {1: 0, 2: 0}
                def forged(path):
                    if "/jobs?" in path:
                        rid = int(path.split("/runs/")[1].split("/")[0])
                        seen[rid] += 1
                        record = job(rid + 1000)
                        if rid == 1 and seen[rid] == 2:
                            record[name] = replacement
                        return {"total_count": 1, "jobs": [record]}, ""
                    return get(path)
                with self.assertRaisesRegex(collection.InventoryError, "invalid_candidate_job"):
                    collection.collect(forged, clock=NOW)

    def test_second_pass_candidate_job_can_keep_valid_positive_runner_id(self):
        get, _ = api_snapshot(run(1), run(2))
        calls = {1: 0, 2: 0}
        def stable(path):
            if "/jobs?" in path:
                rid = int(path.split("/runs/")[1].split("/")[0])
                calls[rid] += 1
                record = job(rid + 1000)
                record["runner_id"] = 10
                return {"total_count": 1, "jobs": [record]}, ""
            return get(path)
        result = collection.collect(stable, clock=NOW)
        self.assertEqual(calls, {1: 2, 2: 2})
        self.assertEqual(
            policy.assess(result, expected_main_sha=SHA, now=NOW)["decision"],
            "REVIEW_ONLY",
        )

    def test_second_scan_with_malformed_identity_is_denied(self):
        get, _ = api_snapshot(run(1))
        count = [0]
        def fetch(path):
            if path == RUNS:
                count[0] += 1
                if count[0] == 2:
                    changed = run(1)
                    changed["head_repository"] = None
                    return {"total_count": 1, "workflow_runs": [changed]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "invalid_run_identity"):
            collection.collect(fetch, clock=NOW)


    def test_candidate_job_started_after_first_read_denied(self):
        """A stable run ID is insufficient once an actual job has started."""
        get, _ = api_snapshot(run(1), run(2))
        observations = {1: 0, 2: 0}
        def changing(path):
            if "/jobs?" in path:
                ident = int(path.split("/runs/")[1].split("/")[0])
                observations[ident] += 1
                record = job(ident + 1000)
                if ident == 2 and observations[ident] == 2:
                    record["status"] = "in_progress"
                return {"total_count": 1, "jobs": [record]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "candidate_job_changed"):
            collection.collect(changing, clock=NOW)
        self.assertEqual(observations, {1: 2, 2: 2})

    def test_candidate_runner_assignment_change_denied(self):
        get, _ = api_snapshot(run(1), run(2))
        observations = {1: 0, 2: 0}
        def changing(path):
            if "/jobs?" in path:
                ident = int(path.split("/runs/")[1].split("/")[0])
                observations[ident] += 1
                record = job(ident + 1000)
                if ident == 1 and observations[ident] == 2:
                    record["runner_id"] = 404
                return {"total_count": 1, "jobs": [record]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "candidate_job_changed"):
            collection.collect(changing, clock=NOW)

    def test_nonmatching_workflows_do_not_consume_candidate_recheck_budget(self):
        first = run(1)
        second = run(2)
        second["workflow_id"] = 99
        get, calls = api_snapshot(first, second)
        result = collection.collect(get, clock=NOW)
        self.assertEqual(len(result["run_pages"][0]["runs"]), 2)
        self.assertEqual(sum("/jobs?" in path for path in calls), 2)

    def test_candidate_signature_requires_valid_labels_and_ids(self):
        for bad in ({"id": True, "labels": []}, {"id": 1, "labels": [True]},
                    {"id": 0, "labels": ["vps"]}, {"id": 1, "labels": None}):
            with self.subTest(bad=bad):
                with self.assertRaises(collection.InventoryError):
                    collection._job_signature(bad)

    def test_excessive_candidate_rechecks_are_denied(self):
        rows = [run(i) for i in range(1, 112)]
        second_page = ROOT + "/actions/runs?status=queued&per_page=100&page=2"
        next_link = '<https://api.github.com' + second_page + '>; rel="next"'
        reads = {}
        def api(path):
            reads[path] = reads.get(path, 0) + 1
            if path == MAIN:
                return main()
            if path == RUNS:
                return {"total_count": 111, "workflow_runs": rows[:100]}, next_link
            if path == second_page:
                return {"total_count": 111, "workflow_runs": rows[100:]}, ""
            if "/jobs?" in path:
                ident = int(path.split("/runs/")[1].split("/")[0])
                return {"total_count": 1, "jobs": [job(ident + 1000)]}, ""
            raise AssertionError(path)
        with self.assertRaisesRegex(collection.InventoryError, "candidate_recheck_cap_exceeded"):
            collection.collect(api, clock=NOW)
        # Every original job is observed, but no unsafe or unbounded
        # second-pass job reads are started beyond the cap.
        self.assertEqual(sum("/jobs?" in path for path in reads), 111)


    def test_duplicate_run_id_across_pages_denied_before_jobs_fetch(self):
        second = ROOT + "/actions/runs?status=queued&per_page=100&page=2"
        marker = '<https://api.github.com' + second + '>; rel="next"'
        paths = []
        def api(path):
            paths.append(path)
            if path == MAIN:
                return main()
            if path == RUNS:
                return {"total_count": 2, "workflow_runs": [run(1)]}, marker
            if path == second:
                return {"total_count": 2, "workflow_runs": [run(1)]}, ""
            raise AssertionError("job endpoint must not be reached")
        with self.assertRaisesRegex(collection.InventoryError, "duplicate_or_invalid_paged_identity"):
            collection.collect(api, clock=NOW)
        self.assertFalse(any("/jobs?" in path for path in paths))

    def test_duplicate_job_ids_within_page_denied(self):
        get, _ = api_snapshot(run(1))
        def api(path):
            if "/jobs?" in path:
                return {"total_count": 2, "jobs": [job(1001), job(1001)]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "duplicate_or_invalid_paged_identity"):
            collection.collect(api, clock=NOW)

    def test_invalid_run_page_id_denied_early(self):
        for ident in (True, 0, "1", -1):
            with self.subTest(ident=ident):
                record = run(1)
                record["id"] = ident
                get, _ = api_snapshot()
                def api(path):
                    if path == RUNS:
                        return {"total_count": 1, "workflow_runs": [record]}, ""
                    return get(path)
                with self.assertRaisesRegex(collection.InventoryError, "duplicate_or_invalid_paged_identity"):
                    collection.collect(api, clock=NOW)

    def test_truncated_second_page_denied(self):
        def fetch(path):
            if path == MAIN:
                return main()
            if path == RUNS:
                return {"total_count": 2, "workflow_runs": [run(1)]}, ""
            raise AssertionError(path)
        with self.assertRaisesRegex(collection.InventoryError, "truncated_inventory"):
            collection.collect(fetch, clock=NOW)

    def test_missing_first_page_link_denied_if_total_larger(self):
        get, _ = api_snapshot(run(1))
        def fetch(path):
            if path == RUNS:
                return {"total_count": 101, "workflow_runs": [run(1)]}, ""
            return get(path)
        with self.assertRaises(collection.InventoryError):
            collection.collect(fetch, clock=NOW)

    def test_malicious_cross_origin_next_cursor_denied(self):
        link = '<https://evil.example/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100&page=2>; rel="next"'
        with self.assertRaisesRegex(collection.InventoryError, "cross_origin_next_link"):
            collection._next_link(link, expected_path=ROOT + "/actions/runs",
                                  expected_page=1,
                                  required_query={"status": "queued", "per_page": "100"})

    def test_duplicate_next_links_denied(self):
        item = '<https://api.github.com' + ROOT + '/actions/runs?status=queued&per_page=100&page=2>; rel="next"'
        with self.assertRaises(collection.InventoryError):
            collection._next_link(item + "," + item,
                                  expected_path=ROOT + "/actions/runs", expected_page=1,
                                  required_query={"status": "queued", "per_page": "100"})

    def test_replayed_cursor_denied(self):
        item = '<https://api.github.com' + ROOT + '/actions/runs?status=queued&per_page=100&page=1>; rel="next"'
        with self.assertRaisesRegex(collection.InventoryError, "untrusted_next_cursor"):
            collection._next_link(item, expected_path=ROOT + "/actions/runs", expected_page=1,
                                  required_query={"status": "queued", "per_page": "100"})

    def test_changed_main_after_queue_denied(self):
        get, _ = api_snapshot(run(1))
        hits = [0]
        def fetch(path):
            if path == MAIN:
                hits[0] += 1
                return main("b" * 40 if hits[0] == 2 else SHA)
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "main_moved"):
            collection.collect(fetch, clock=NOW)

    def test_race_in_latest_queued_inventory_denied(self):
        get, _ = api_snapshot(run(1))
        seen = [0]
        def fetch(path):
            if path == RUNS:
                seen[0] += 1
                if seen[0] == 2:
                    return {"total_count": 1, "workflow_runs": [run(2)]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "queue_changed"):
            collection.collect(fetch, clock=NOW)


    def test_cross_run_job_evidence_denied(self):
        get, _ = api_snapshot(run(1))
        def swapped(path):
            if "/jobs?" in path:
                return {"total_count": 1, "jobs": [job(1001, parent=999)]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
            collection.collect(swapped, clock=NOW)

    def test_missing_job_parent_run_identity_denied(self):
        get, _ = api_snapshot(run(1))
        def missing(path):
            if "/jobs?" in path:
                detached = job(1001)
                detached.pop("run_id")
                return {"total_count": 1, "jobs": [detached]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
            collection.collect(missing, clock=NOW)

    def test_noncanonical_parent_identity_denied(self):
        for parent in (True, "1", 0):
            with self.subTest(parent=parent):
                get, _ = api_snapshot(run(1))
                def malformed(path):
                    if "/jobs?" in path:
                        return {"total_count": 1, "jobs": [job(1001, parent=parent)]}, ""
                    return get(path)
                with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
                    collection.collect(malformed, clock=NOW)


    def test_job_attempt_must_match_original_run_attempt(self):
        get, _ = api_snapshot(run(1))
        def swapped(path):
            if "/jobs?" in path:
                record = job(1001)
                record["run_attempt"] = 2
                return {"total_count": 1, "jobs": [record]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
            collection.collect(swapped, clock=NOW)

    def test_job_sha_and_branch_must_match_run(self):
        for key, value in (("head_sha", "b" * 40), ("head_branch", "worker/unsafe")):
            with self.subTest(key=key):
                get, _ = api_snapshot(run(1))
                def swapped(path):
                    if "/jobs?" in path:
                        record = job(1001)
                        record[key] = value
                        return {"total_count": 1, "jobs": [record]}, ""
                    return get(path)
                with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
                    collection.collect(swapped, clock=NOW)

    def test_missing_boolean_or_invalid_attempt_denied(self):
        for attempt in (None, True, 0, -1, "1", 2.0):
            with self.subTest(attempt=attempt):
                get, _ = api_snapshot(run(1))
                def swapped(path):
                    if "/jobs?" in path:
                        record = job(1001)
                        record["run_attempt"] = attempt
                        return {"total_count": 1, "jobs": [record]}, ""
                    return get(path)
                with self.assertRaisesRegex(collection.InventoryError, "cross_run_job_evidence"):
                    collection.collect(swapped, clock=NOW)

    def test_invalid_parent_attempt_denied_before_job_read(self):
        for attempt in (None, 0, True, "1"):
            with self.subTest(attempt=attempt):
                parent = run(1)
                parent["run_attempt"] = attempt
                get, _ = api_snapshot(parent)
                with self.assertRaisesRegex(collection.InventoryError, "invalid_run_provenance"):
                    collection.collect(get, clock=NOW)

    def test_retried_run_and_matching_job_attempt_are_allowed_as_observation(self):
        parent = run(1)
        parent["run_attempt"] = 3
        get, _ = api_snapshot(parent)
        def matched(path):
            if "/jobs?" in path:
                record = job(1001)
                record["run_attempt"] = 3
                return {"total_count": 1, "jobs": [record]}, ""
            return get(path)
        snapshot = collection.collect(matched, clock=NOW)
        self.assertEqual(snapshot["run_pages"][0]["runs"][0]["id"], 1)
        outcome = policy.assess(snapshot, expected_main_sha=SHA, now=NOW)
        self.assertEqual(outcome["decision"], "NOOP")
        self.assertFalse(outcome["mutation_authorized"])

    def test_missing_jobs_denied(self):
        get, _ = api_snapshot(run(1))
        def fetch(path):
            if "/jobs?" in path:
                return {"total_count": 0, "jobs": []}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "run_without_valid_job"):
            collection.collect(fetch, clock=NOW)

    def test_unbounded_total_denied(self):
        get, _ = api_snapshot(run(1))
        def fetch(path):
            if path == RUNS:
                return {"total_count": 1000, "workflow_runs": [run(1)]}, ""
            return get(path)
        with self.assertRaisesRegex(collection.InventoryError, "invalid_total_count"):
            collection.collect(fetch, clock=NOW)

    def test_bad_repo_context_preserved_as_denial(self):
        foreign = run(1)
        foreign["head_repository"]["full_name"] = "elsewhere/fork"
        get, _ = api_snapshot(foreign)
        evidence = collection.collect(get, clock=NOW)
        self.assertEqual(policy.assess(evidence, expected_main_sha=SHA, now=NOW)["decision"], "DENY")

    def test_api_adapter_never_uses_post(self):
        adapter = collection.ReadOnlyAPI("fake-token")
        class Response:
            status = 200
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _):
                return b'{"commit":{"sha":"' + SHA.encode() + b'"}}'
        class FakeOpener:
            def open(self, request, timeout):
                self_ref.assertEqual(request.get_method(), "GET")
                self_ref.assertEqual(timeout, 12)
                self_ref.assertTrue(request.full_url.startswith("https://api.github.com/repos/Zennay/zCloud/"))
                return Response()
        self_ref = self
        adapter.opener = FakeOpener()
        payload, link = adapter.get(MAIN)
        self.assertEqual(payload["commit"]["sha"], SHA)
        self.assertEqual(link, "")

    def test_adapter_rejects_duplicate_json_objects(self):
        adapter = collection.ReadOnlyAPI("fake-token")
        class Response:
            status = 200
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _):
                return b'{"a":1,"a":2}'
        adapter.opener = type("Fake", (), {"open": lambda _self, req, timeout: Response()})()
        with self.assertRaises(collection.InventoryError):
            adapter.get(MAIN)


    def test_github_token_cannot_read_arbitrary_repository_paths(self):
        observer = collection.ReadOnlyAPI("fake-token")
        class DenyNetwork:
            def open(self, request, timeout):
                raise AssertionError("No network permitted for disallowed path")
        observer.opener = DenyNetwork()
        forbidden = (
            "/repos/Zennay/zCloud/actions/secrets",
            "/repos/Zennay/zCloud/contents/.env",
            "/repos/Zennay/zCloud/branches/worker",
            "/repos/Zennay/zCloud/actions/runs/1/cancel",
            "/repos/Zennay/zCloud/actions/runs?status=completed&per_page=100&page=1",
            "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100&page=0",
            "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100&page=1&extra=1",
            "/repos/Zennay/zCloud/actions/runs/0/jobs?per_page=100&page=1",
            "/repos/Zennay/zCloud/actions/runs/1/jobs?per_page=100&page=0",
            "/repos/Zennay/zCloud/actions/runs/1/jobs?per_page=100&page=1#fragment",
            "/repos/Zennay/zCloud/../another-repo/actions/runs",
            "https://api.github.com/repos/Zennay/zCloud/branches/main",
            "/repos/Zennay/zCloud/branches/main?unexpected=1",
        )
        for bad in forbidden:
            with self.subTest(path=bad):
                with self.assertRaisesRegex(collection.InventoryError, "noncanonical_api_path"):
                    observer.get(bad)
        for bad in (None, 12, True):
            with self.subTest(path=bad):
                with self.assertRaisesRegex(collection.InventoryError, "noncanonical_api_path"):
                    observer.get(bad)

    def test_only_three_readonly_github_route_families_admitted(self):
        accepted = (
            MAIN,
            RUNS,
            ROOT + "/actions/runs?status=queued&per_page=100&page=2",
            ROOT + "/actions/runs/315/jobs?per_page=100&page=1",
            ROOT + "/actions/runs/315/jobs?per_page=100&page=3",
        )
        for path in accepted:
            with self.subTest(path=path):
                self.assertIsNotNone(collection._ALLOWED_GET_PATH.fullmatch(path))



    def test_error_redaction_never_crashes_on_unhashable_or_hostile_codes(self):
        for value in ([], {}, ["secret"], {"token": "secret"},
                      123, True, "secret" * 1000):
            with self.subTest(value=value):
                outcome = collection.safe_error_category(
                    collection.InventoryError(value)
                )
                self.assertEqual(outcome, "github_inventory_untrusted")
                self.assertNotIn("secret", outcome)

    def test_unknown_error_type_never_leaks_message(self):
        self.assertEqual(
            collection.safe_error_category(ValueError("secret-credential")),
            "github_inventory_untrusted",
        )
        self.assertEqual(
            collection.safe_error_category(collection.InventoryError()),
            "github_inventory_untrusted",
        )

    def test_collector_error_category_is_allowlisted_and_redacted(self):
        pairs = {
            "truncated_inventory": "incomplete_queue_pages",
            "duplicate_or_invalid_paged_identity": "invalid_job_identity",
            "cross_run_job_evidence": "invalid_job_identity",
            "candidate_job_changed_during_inventory": "job_state_changed",
            "queue_changed_during_inventory": "job_state_changed",
            "main_moved_during_inventory": "main_changed",
            "snapshot_acquisition_too_slow": "observation_too_slow",
            "candidate_recheck_cap_exceeded": "queue_too_large",
        }
        for code, reason in pairs.items():
            with self.subTest(code=code):
                self.assertEqual(
                    collection.safe_error_category(collection.InventoryError(code)), reason
                )
        # Unknown codes or raw data can never reach the operator.
        for bad in ("bearer TOKEN-private", "https://evil.example/secret", None):
            self.assertEqual(
                collection.safe_error_category(collection.InventoryError(bad)),
                "github_inventory_untrusted",
            )
        self.assertEqual(
            collection.safe_error_category(ValueError("private data")),
            "github_inventory_untrusted",
        )

    def test_api_token_validation_denies_newlines(self):
        with self.assertRaises(collection.InventoryError):
            collection.ReadOnlyAPI("token\r\ninjected")

    def test_api_token_header_boundary_denies_unsafe_credentials_before_request(self):
        for bad in (
            "", " token", "token ", "token\tsecret", "token\x00bad",
            "token\nsecret", "token\rsecret", "token:secret",
            "token/secret", "token?secret", "tokén", "token\ud800",
            "token" * 1025, True, 33, {}, ["secret"],
        ):
            with self.subTest(credential=type(bad).__name__):
                with self.assertRaisesRegex(collection.InventoryError, "invalid_token"):
                    collection.ReadOnlyAPI(bad)

    def test_api_token_header_boundary_preserves_ascii_scoped_credentials(self):
        for token in ("ghs_fake.token-123", "github_pat_safe_ABC-0123", "fake-token"):
            with self.subTest(token=token):
                adapter = collection.ReadOnlyAPI(token)
                self.assertEqual(adapter.token, token)


if __name__ == "__main__":
    unittest.main()
