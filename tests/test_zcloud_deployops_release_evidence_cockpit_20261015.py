"""Adversarial offline regressions for the add-only deploy-ops evidence cockpit."""
import importlib.util
import datetime as dt
import json
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deployops_release_evidence_cockpit_20261015.py"
SPEC = importlib.util.spec_from_file_location("readiness_cockpit", SOURCE)
cockpit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cockpit)

SHA = "a" * 40
ALT_SHA = "b" * 40
REF = "/repos/Zennay/zCloud/git/ref/heads/main"
STAT = f"/repos/Zennay/zCloud/commits/{SHA}/statuses?per_page=100&page=1"
STAT2 = f"/repos/Zennay/zCloud/commits/{SHA}/statuses?per_page=100&page=2"
RUNS = ("/repos/Zennay/zCloud/actions/workflows/zcloud-vps-deploy.yml/runs"
        "?branch=main&per_page=100&page=1")
JOBS = "/repos/Zennay/zCloud/actions/runs/92/jobs?per_page=100&page=1"
RUN_DETAIL = "/repos/Zennay/zCloud/actions/runs/92"
RUNS_PREFIX = RUNS.rsplit("=", 1)[0] + "="


def ref(sha=SHA):
    return {"object": {"sha": sha, "type": "commit"}}


def run(sha=SHA, state="completed", conclusion="success"):
    return {"id": 92, "run_attempt": 1, "head_sha": sha, "head_branch": "main",
            "event": "workflow_run", "status": state, "conclusion": conclusion,
            "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def job(status="completed", runner_id=21, runner_name="zcloud-vps-1", *,
        job_id=10, run_id=92, run_attempt=1, head_sha=SHA, conclusion=None):
    if conclusion is None and status == "completed":
        conclusion = "success"
    return {"id": job_id, "run_id": run_id, "run_attempt": run_attempt,
            "head_sha": head_sha,
            "labels": ["self-hosted", "zcloud", "vps"], "status": status,
            "conclusion": conclusion, "runner_id": runner_id, "runner_name": runner_name}


def status(state="success"):
    return [{"context": "zcloud/vps-production", "id": 20,
             "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "state": state}]


class FakeAPI:
    def __init__(self):
        self.pages = {
            REF: [ref(), ref()],
            STAT: [status()],
            RUNS: [{"workflow_runs": [run()]}],
            JOBS: [{"total_count": 1, "jobs": [job()]}],
        }
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == RUN_DETAIL and path not in self.pages:
            # The real API returns the same selected run through a separate
            # URL. Allow tests to override it to simulate a racing rerun.
            candidate = self.pages[RUNS][0]["workflow_runs"]
            return dict(candidate[0]) if candidate else None
        if path not in self.pages or not self.pages[path]:
            raise cockpit.EvidenceError("missing_fixture")
        values = self.pages[path]
        return values.pop(0) if len(values) > 1 else values[0]


class ReleaseEvidenceTests(unittest.TestCase):
    def test_full_positive_observation_never_authorizes_release(self):
        data = cockpit.collect(FakeAPI())
        self.assertEqual(data["production"]["state"], "success")
        self.assertEqual(data["deploy"]["runner_route"], "ASSIGNED_TO_EXPECTED")
        self.assertEqual(data["warnings"], [])
        self.assertFalse(data["deployment_authorized"])
        self.assertFalse(data["release_authorized"])
        self.assertFalse(data["mutations_performed"])

    def test_main_revalidated_and_drift_denied(self):
        api = FakeAPI()
        api.pages[REF] = [ref(), ref(ALT_SHA)]
        with self.assertRaisesRegex(cockpit.EvidenceError, "main_moved"):
            cockpit.collect(api)
        self.assertEqual(api.calls.count(REF), 2)

    def test_stale_historical_runner_does_not_look_fresh(self):
        api = FakeAPI()
        old = run()
        old["updated_at"] = "2020-01-01T00:00:00Z"
        api.pages[RUNS] = [{"workflow_runs": [old]}]
        data = cockpit.collect(api)
        self.assertGreater(data["deploy"]["run_age_seconds"], 900)
        self.assertIn("deploy_observation_stale_or_missing", data["warnings"])
        self.assertFalse(data["deployment_authorized"])

    def test_future_dated_deploy_report_denied(self):
        api = FakeAPI()
        future = run()
        future["updated_at"] = "2099-01-01T00:00:00Z"
        api.pages[RUNS] = [{"workflow_runs": [future]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "future_deploy_evidence"):
            cockpit.collect(api)

    def test_invalid_calendar_timestamp_denied(self):
        api = FakeAPI()
        invalid = run()
        invalid["updated_at"] = "2026-13-44T00:00:00Z"
        api.pages[RUNS] = [{"workflow_runs": [invalid]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "invalid_deploy_updated_at"):
            cockpit.collect(api)

    def test_missing_production_status_is_a_warning(self):
        api = FakeAPI()
        api.pages[STAT] = [[]]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["state"], "missing")
        self.assertIn("production_not_success", data["warnings"])

    def test_old_green_status_loses_to_newer_failure(self):
        api = FakeAPI()
        api.pages[STAT] = [[
            {"context": "zcloud/vps-production", "state": "success",
             "id": 1, "created_at": "2026-10-15T03:00:00Z"},
            {"context": "zcloud/vps-production", "state": "failure",
             "id": 2, "created_at": "2026-10-15T04:00:00Z"},
        ]]
        self.assertEqual(cockpit.collect(api)["production"]["state"], "failure")

    def test_production_old_success_warns_not_fresh(self):
        api = FakeAPI()
        api.pages[STAT] = [[{"context": "zcloud/vps-production",
                            "created_at": "2020-01-01T00:00:00Z",
                            "id": 55, "state": "success"}]]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["state"], "success")
        self.assertGreater(data["production"]["last_status_age_seconds"], 900)
        self.assertIn("production_observation_stale", data["warnings"])
        self.assertIn("Leeftijd productiestatus", cockpit.render_html(data))
        self.assertFalse(data["release_authorized"])

    def test_production_invalid_calendar_timestamp_denied(self):
        api = FakeAPI()
        api.pages[STAT] = [[{"context": "zcloud/vps-production",
                            "created_at": "2026-19-44T00:00:00Z",
                            "id": 55, "state": "success"}]]
        with self.assertRaisesRegex(cockpit.EvidenceError, "invalid_production_timestamp"):
            cockpit.collect(api)

    def test_production_future_timestamp_denied(self):
        api = FakeAPI()
        api.pages[STAT] = [[{"context": "zcloud/vps-production",
                            "created_at": "2099-01-01T00:00:00Z",
                            "id": 55, "state": "success"}]]
        with self.assertRaisesRegex(cockpit.EvidenceError, "future_production_evidence"):
            cockpit.collect(api)

    def test_production_repeated_id_denied(self):
        api = FakeAPI()
        repeated = dict(status()[0])
        api.pages[STAT] = [[repeated, dict(repeated)]]
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_production_status_id"):
            cockpit.collect(api)

    def test_production_status_changes_between_reads_denied(self):
        api = FakeAPI()
        api.pages[STAT] = [status("success"), status("failure")]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "production_status_changed_during_observation"):
            cockpit.collect(api)
        self.assertEqual(api.calls.count(STAT), 2)

    def test_new_production_event_even_same_green_state_denied(self):
        api = FakeAPI()
        first = status()
        second = [dict(first[0], id=21)]
        api.pages[STAT] = [first, second]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "production_status_changed_during_observation"):
            cockpit.collect(api)

    def test_production_id_is_bound_to_snapshot(self):
        data = cockpit.collect(FakeAPI())
        self.assertEqual(data["production"]["latest_status_id"], 20)
        self.assertTrue(data["production"]["latest_status_at_utc"])
        self.assertFalse(data["deployment_authorized"])

    def test_production_status_pages_include_second_page_failure(self):
        api = FakeAPI()
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        batch = [{"context": "zcloud/vps-production", "id": 1000 + i,
                  "created_at": "2020-01-01T00:00:00Z", "state": "success"}
                 for i in range(100)]
        api.pages[STAT] = [batch]
        api.pages[STAT2] = [[{"context": "zcloud/vps-production",
                              "id": 3001, "created_at": now, "state": "failure"}]]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["matching_events"], 101)
        self.assertEqual(data["production"]["state"], "failure")
        self.assertEqual(api.calls.count(STAT2), 2)
        self.assertFalse(data["release_authorized"])

    def test_production_status_page_two_duplicate_event_id_denied(self):
        api = FakeAPI()
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        batch = [{"context": "zcloud/vps-production", "id": 1000 + i,
                  "created_at": now, "state": "success"} for i in range(100)]
        api.pages[STAT] = [batch]
        api.pages[STAT2] = [[dict(batch[0])]]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "duplicate_production_status_id"):
            cockpit.collect(api)

    def test_production_status_page_two_missing_denies(self):
        api = FakeAPI()
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        api.pages[STAT] = [[{"context": "zcloud/vps-production", "id": i + 1000,
                             "created_at": now, "state": "pending"}
                            for i in range(100)]]
        with self.assertRaises(cockpit.EvidenceError):
            cockpit.collect(api)

    def test_old_status_event_changed_but_latest_green_still_denied(self):
        api = FakeAPI()
        fresh = status()[0]
        old = {"context": "zcloud/vps-production", "id": 19,
               "created_at": "2020-01-01T00:00:00Z", "state": "pending"}
        original = [fresh, old]
        changed = [fresh, dict(old, state="failure")]
        api.pages[STAT] = [original, changed]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "production_status_changed_during_observation"):
            cockpit.collect(api)
        self.assertEqual(api.calls.count(STAT), 2)

    def test_status_event_order_change_is_not_a_false_drift(self):
        api = FakeAPI()
        fresh = status()[0]
        old = {"context": "zcloud/vps-production", "id": 19,
               "created_at": "2020-01-01T00:00:00Z", "state": "pending"}
        api.pages[STAT] = [[fresh, old], [old, fresh]]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["state"], "success")
        self.assertEqual(len(data["production"]["status_events_digest"]), 64)
        self.assertFalse(data["deployment_authorized"])

    def test_status_digest_is_present_even_when_no_status_exists(self):
        api = FakeAPI()
        api.pages[STAT] = [[]]
        data = cockpit.collect(api)
        self.assertEqual(len(data["production"]["status_events_digest"]), 64)
        self.assertIn("production_not_success", data["warnings"])

    def test_queued_job_with_runner_zero_is_not_proof(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job("queued", 0, None)]}]
        result = cockpit.collect(api)
        self.assertEqual(result["deploy"]["runner_route"], "QUEUED_UNASSIGNED")
        self.assertIn("runner_route_not_proven", result["warnings"])

    def test_unexpected_runner_does_not_count_as_canonical(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(runner_name="temporary-runner")]}]
        self.assertEqual(cockpit.collect(api)["deploy"]["runner_route"], "UNEXPECTED_RUNNER")

    def test_ambiguous_multiple_runner_jobs_prioritizes_queue(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 2, "jobs": [
            job(), job("queued", 0, None, job_id=11)]}]
        self.assertEqual(cockpit.collect(api)["deploy"]["runner_route"], "QUEUED_UNASSIGNED")

    def test_incomplete_job_pagination_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 101, "jobs": [job()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "incomplete_deploy_jobs"):
            cockpit.collect(api)

    def test_short_job_page_mismatch_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 2, "jobs": [job()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "job_count_mismatch"):
            cockpit.collect(api)

    def test_no_exact_main_deploy_is_unknown_not_green(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": [run(sha=ALT_SHA)]}]
        data = cockpit.collect(api)
        self.assertEqual(data["deploy"]["runner_route"], "UNVERIFIED")
        self.assertIn("deploy_run_not_success", data["warnings"])

    def test_noncanonical_run_provenance_denied(self):
        api = FakeAPI()
        bad = run()
        bad["head_branch"] = "feature/evil"
        api.pages[RUNS] = [{"workflow_runs": [bad]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "unexpected_deploy_provenance"):
            cockpit.collect(api)

    def test_cross_run_job_misattribution_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(run_id=93)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "mismatched_deploy_job_identity"):
            cockpit.collect(api)

    def test_cross_attempt_job_misattribution_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(run_attempt=2)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "mismatched_deploy_job_identity"):
            cockpit.collect(api)

    def test_cross_sha_job_misattribution_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(head_sha=ALT_SHA)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "mismatched_deploy_job_identity"):
            cockpit.collect(api)

    def test_boolean_run_id_in_job_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(run_id=True)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "mismatched_deploy_job_identity"):
            cockpit.collect(api)

    def test_duplicate_job_id_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 2, "jobs": [job(), job()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_deploy_job"):
            cockpit.collect(api)

    def test_assigned_but_failed_runner_is_not_success(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(conclusion="failure")]}]
        data = cockpit.collect(api)
        self.assertEqual(data["deploy"]["runner_route"], "ASSIGNED_TO_EXPECTED")
        self.assertEqual(data["deploy"]["runner_job_result"], "FAILED")
        self.assertIn("runner_job_not_success", data["warnings"])
        self.assertFalse(data["deployment_authorized"])
        self.assertIn("VPS-jobuitkomst", cockpit.render_html(data))

    def test_queued_runner_job_is_not_completed(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": [run(state="in_progress", conclusion=None)]}]
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job("queued", 0, None)]}]
        data = cockpit.collect(api)
        self.assertEqual(data["deploy"]["runner_job_result"], "NOT_TERMINAL")
        self.assertIn("runner_job_not_success", data["warnings"])

    def test_nonterminal_job_with_conclusion_denied(self):
        api = FakeAPI()
        pending = job("queued", 0, None)
        pending["conclusion"] = "success"
        api.pages[JOBS] = [{"total_count": 1, "jobs": [pending]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "nonterminal_job_has_conclusion"):
            cockpit.collect(api)

    def test_finished_job_without_conclusion_denied(self):
        api = FakeAPI()
        finished = job()
        finished["conclusion"] = None
        api.pages[JOBS] = [{"total_count": 1, "jobs": [finished]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "invalid_terminal_job_conclusion"):
            cockpit.collect(api)

    def test_new_retry_attempt_during_job_read_denied(self):
        api = FakeAPI()
        changed = run()
        changed["run_attempt"] = 2
        api.pages[RUN_DETAIL] = [changed]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_changed_during_job_read"):
            cockpit.collect(api)

    def test_deploy_conclusion_changed_during_job_read_denied(self):
        api = FakeAPI()
        changed = run()
        changed["conclusion"] = "failure"
        api.pages[RUN_DETAIL] = [changed]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_changed_during_job_read"):
            cockpit.collect(api)

    def test_run_updated_timestamp_changes_during_job_read_denied(self):
        api = FakeAPI()
        changed = run()
        changed["updated_at"] = "2026-10-15T03:00:00Z"
        api.pages[RUN_DETAIL] = [changed]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_changed_during_job_read"):
            cockpit.collect(api)

    def test_duplicate_run_attempt_in_snapshot_denied(self):
        api = FakeAPI()
        same = run()
        api.pages[RUNS] = [{"workflow_runs": [same, dict(same)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_paginated_deploy_run"):
            cockpit.collect(api)

    def test_completed_run_missing_conclusion_is_denied(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": [run(conclusion=None)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "completed_deploy_missing_conclusion"):
            cockpit.collect(api)

    def test_queued_run_with_old_success_conclusion_is_denied(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": [run(state="queued", conclusion="success")]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "nonterminal_deploy_has_conclusion"):
            cockpit.collect(api)

    def test_deploy_page_two_can_find_same_sha_when_first_page_full(self):
        api = FakeAPI()
        older = [dict(run(sha=ALT_SHA), id=i + 1000) for i in range(100)]
        api.pages[RUNS] = [{"workflow_runs": older}]
        api.pages[RUNS_PREFIX + "2"] = [{"workflow_runs": [run()]}]
        api.pages[RUN_DETAIL] = [run()]
        data = cockpit.collect(api)
        self.assertEqual(data["deploy"]["run_id"], 92)
        self.assertEqual(data["deploy"]["run_pages_scanned"], 2)
        self.assertEqual(data["deploy"]["runs_scanned"], 101)
        self.assertFalse(data["deployment_authorized"])

    def test_deploy_duplicate_run_id_across_pages_denied(self):
        api = FakeAPI()
        older = [dict(run(sha=ALT_SHA), id=i + 1000) for i in range(100)]
        api.pages[RUNS] = [{"workflow_runs": older}]
        api.pages[RUNS_PREFIX + "2"] = [
            {"workflow_runs": [dict(run(sha=ALT_SHA), id=1005)]}
        ]
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_paginated_deploy_run"):
            cockpit.collect(api)

    def test_deploy_large_repo_is_explicitly_partial_not_healthy(self):
        api = FakeAPI()
        for page in range(1, 6):
            batch = [dict(run(sha=ALT_SHA), id=page * 1000 + i)
                     for i in range(100)]
            api.pages[RUNS_PREFIX + str(page)] = [
                {"workflow_runs": batch, "total_count": 1200}
            ]
        data = cockpit.collect(api)
        self.assertEqual(data["deploy"]["runs_scanned"], 500)
        self.assertEqual(data["deploy"]["run_pages_scanned"], 5)
        self.assertFalse(data["deploy"]["run_inventory_complete"])
        self.assertFalse(data["deploy"]["run_inventory_count_verified"])
        self.assertIn("deploy_run_inventory_incomplete", data["warnings"])
        self.assertEqual(data["deploy"]["runner_route"], "UNVERIFIED")
        self.assertFalse(data["deployment_authorized"])
        self.assertIn("volledige historie: NEE (gedeeltelijk)", cockpit.render_html(data))

    def test_deploy_large_repo_partial_even_without_total_count(self):
        api = FakeAPI()
        for page in range(1, 6):
            batch = [dict(run(sha=ALT_SHA), id=page * 1000 + i)
                     for i in range(100)]
            api.pages[RUNS_PREFIX + str(page)] = [{"workflow_runs": batch}]
        data = cockpit.collect(api)
        self.assertIn("deploy_run_inventory_incomplete", data["warnings"])
        self.assertFalse(data["release_authorized"])

    def test_deploy_page_two_missing_or_malformed_denies(self):
        api = FakeAPI()
        api.pages[RUNS] = [
            {"workflow_runs": [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]}
        ]
        api.pages[RUNS_PREFIX + "2"] = [{"workflow_runs": None}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "invalid_workflow_runs"):
            cockpit.collect(api)

    def test_deploy_second_page_changes_between_reads_denied(self):
        api = FakeAPI()
        first = [dict(run(sha=ALT_SHA), id=i + 1000) for i in range(100)]
        original = {"workflow_runs": [run()]}
        changed = {"workflow_runs": [dict(run(), id=4002)]}
        api.pages[RUNS] = [{"workflow_runs": first}]
        api.pages[RUNS_PREFIX + "2"] = [original, changed]
        api.pages[RUN_DETAIL] = [run()]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_page_drift"):
            cockpit.collect(api)
        self.assertEqual(api.calls.count(RUNS_PREFIX + "2"), 2)

    def test_deploy_second_page_stable_but_still_not_authorized(self):
        api = FakeAPI()
        first = [dict(run(sha=ALT_SHA), id=i + 1000) for i in range(100)]
        api.pages[RUNS] = [{"workflow_runs": first}]
        api.pages[RUNS_PREFIX + "2"] = [{"workflow_runs": [run()]}]
        api.pages[RUN_DETAIL] = [run()]
        data = cockpit.collect(api)
        self.assertEqual(api.calls.count(RUNS_PREFIX + "2"), 2)
        self.assertFalse(data["release_authorized"])
        self.assertEqual(data["deploy"]["runner_job_result"], "SUCCESS")

    def test_deploy_total_count_matches_full_paginated_run_inventory(self):
        api = FakeAPI()
        first = [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]
        api.pages[RUNS] = [{"total_count": 101, "workflow_runs": first}]
        api.pages[RUNS_PREFIX + "2"] = [
            {"total_count": 101, "workflow_runs": [run()]}
        ]
        api.pages[RUN_DETAIL] = [run()]
        data = cockpit.collect(api)
        self.assertTrue(data["deploy"]["run_inventory_count_verified"])
        self.assertEqual(data["deploy"]["runs_scanned"], 101)
        self.assertFalse(data["deployment_authorized"])

    def test_inconsistent_deploy_total_count_after_last_page_denied(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"total_count": 10, "workflow_runs": [run()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "inconsistent_deploy_total_count"):
            cockpit.collect(api)

    def test_boolean_total_count_denied(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"total_count": True, "workflow_runs": [run()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "invalid_deploy_total_count"):
            cockpit.collect(api)

    def test_changing_total_count_between_pages_denied(self):
        api = FakeAPI()
        older = [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]
        api.pages[RUNS] = [{"total_count": 101, "workflow_runs": older}]
        api.pages[RUNS_PREFIX + "2"] = [
            {"total_count": 102, "workflow_runs": [run()]}
        ]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "changing_deploy_total_count"):
            cockpit.collect(api)

    def test_missing_second_page_total_count_denied(self):
        api = FakeAPI()
        older = [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]
        api.pages[RUNS] = [{"total_count": 101, "workflow_runs": older}]
        api.pages[RUNS_PREFIX + "2"] = [{"workflow_runs": [run()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "inconsistent_deploy_total_count_presence"):
            cockpit.collect(api)

    def test_new_second_page_count_when_first_missing_denied(self):
        api = FakeAPI()
        older = [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]
        api.pages[RUNS] = [{"workflow_runs": older}]
        api.pages[RUNS_PREFIX + "2"] = [
            {"total_count": 101, "workflow_runs": [run()]}
        ]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "inconsistent_deploy_total_count_presence"):
            cockpit.collect(api)

    def test_deploy_page_two_timeout_denies(self):
        api = FakeAPI()
        api.pages[RUNS] = [
            {"workflow_runs": [dict(run(sha=ALT_SHA), id=1000 + i) for i in range(100)]}
        ]
        with self.assertRaisesRegex(cockpit.EvidenceError, "missing_fixture"):
            cockpit.collect(api)

    def test_deploy_page_drift_after_job_read_denies(self):
        api = FakeAPI()
        shifted = dict(run(sha=ALT_SHA), id=102)
        api.pages[RUNS] = [{"workflow_runs": [run()]},
                           {"workflow_runs": [shifted]}]
        api.pages[RUN_DETAIL] = [run()]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_page_drift"):
            cockpit.collect(api)

    def test_missing_run_second_read_drift_denies(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": []},
                           {"workflow_runs": [run()]}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "deploy_run_page_drift"):
            cockpit.collect(api)

    def test_deploy_one_page_snapshot_stays_bounded(self):
        data = cockpit.collect(FakeAPI())
        self.assertEqual(data["deploy"]["runs_scanned"], 1)
        self.assertEqual(data["deploy"]["run_pages_scanned"], 1)
        self.assertFalse(data["release_authorized"])

    def test_job_status_changes_after_first_read_denied(self):
        api = FakeAPI()
        old = job("queued", runner_id=0, runner_name=None)
        new = job("in_progress", runner_id=21, runner_name="zcloud-vps-1")
        api.pages[JOBS] = [{"total_count": 1, "jobs": [old]},
                           {"total_count": 1, "jobs": [new]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "deploy_job_changed_during_observation"):
            cockpit.collect(api)
        self.assertEqual(api.calls.count(JOBS), 2)

    def test_job_conclusion_changes_after_first_read_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job()]},
                           {"total_count": 1, "jobs": [job(conclusion="failure")]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "deploy_job_changed_during_observation"):
            cockpit.collect(api)

    def test_new_job_inserted_after_first_read_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job()]},
                           {"total_count": 2, "jobs": [job(), job(job_id=11)]}]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "deploy_job_changed_during_observation"):
            cockpit.collect(api)

    def test_stable_job_snapshot_is_read_twice_and_never_authorizes(self):
        api = FakeAPI()
        data = cockpit.collect(api)
        self.assertEqual(api.calls.count(JOBS), 2)
        self.assertEqual(data["deploy"]["runner_job_result"], "SUCCESS")
        self.assertFalse(data["release_authorized"])

    def test_missing_second_job_snapshot_fails_closed(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job()]}, None]
        with self.assertRaisesRegex(cockpit.EvidenceError,
                                    "deploy_job_changed_during_observation"):
            cockpit.collect(api)

    def test_missing_main_ref_is_denied(self):
        api = FakeAPI()
        api.pages[REF] = [{"object": {"sha": SHA, "type": "tree"}}]
        with self.assertRaisesRegex(cockpit.EvidenceError, "main_ref_not_commit"):
            cockpit.collect(api)

    def test_boolean_ids_and_counters_are_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": True, "jobs": [job()]}]
        with self.assertRaises(cockpit.EvidenceError):
            cockpit.collect(api)
        with self.assertRaises(cockpit.EvidenceError):
            cockpit.integer(True)

    def test_unknown_job_status_denied(self):
        api = FakeAPI()
        api.pages[JOBS] = [{"total_count": 1, "jobs": [job(status="something_else")]}]
        with self.assertRaises(cockpit.EvidenceError):
            cockpit.collect(api)

    def test_duplicate_json_keys_denied(self):
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_json_key"):
            json.loads('{"state": "success", "state": "failure"}',
                       object_pairs_hook=cockpit.unique_pairs)

    def test_unicode_surrogates_denied_in_remote_values_and_keys(self):
        for value in ('{"branch": "\\uD800"}',
                      '{"\\uDC00": "unexpected"}',
                      '["safe", {"value": "\\uDFFF"}]'):
            with self.subTest(value=value):
                decoded = json.loads(value)
                with self.assertRaisesRegex(cockpit.EvidenceError,
                                            "invalid_api_unicode"):
                    cockpit.reject_unpaired_unicode(decoded)

    def test_normal_multilingual_unicode_remains_supported(self):
        data = {"text": "geslaagd ✓ — werkers en café ☕",
                "nested": ["🚀", {"ä":"é"}]}
        self.assertEqual(cockpit.reject_unpaired_unicode(data), data)

    def test_remote_api_surrogate_in_response_denied_without_leak(self):
        import io
        from unittest.mock import patch

        class FakeResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, limit):
                return b'{"branch": "\\uD800"}'

        class FakeOpener:
            def open(self, request, timeout):
                self_request = request
                assert self_request.get_method() == "GET"
                assert timeout == 15
                return FakeResponse()

        with patch.object(cockpit.urllib.request, "build_opener",
                          return_value=FakeOpener()):
            with self.assertRaisesRegex(cockpit.EvidenceError,
                                        "invalid_api_unicode"):
                cockpit.GitHubReadOnly().get(REF)

    def test_unicode_guard_handles_deeply_nested_in_memory_structures(self):
        tree = "gezonde tekst"
        for _ in range(1200):
            tree = [tree]
        self.assertIs(cockpit.reject_unpaired_unicode(tree), tree)
        tree = ["veilig"]
        for _ in range(1200):
            tree = {"k": tree}
        self.assertIs(cockpit.reject_unpaired_unicode(tree), tree)

    def test_unicode_guard_rejects_unbounded_structure(self):
        from unittest.mock import patch
        with patch.object(cockpit, "MAX_BYTES", 3):
            with self.assertRaisesRegex(cockpit.EvidenceError,
                                        "oversized_api_structure"):
                cockpit.reject_unpaired_unicode(["A", "B", "C", "D"])

    def test_json_decoder_recursion_errors_are_sanitized(self):
        from unittest.mock import patch

        class FakeResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, limit):
                return b"[" * 1500 + b"0" + b"]" * 1500

        class FakeOpener:
            def open(self, request, timeout):
                return FakeResponse()

        with patch.object(cockpit.urllib.request, "build_opener",
                          return_value=FakeOpener()):
            # Python runtime recursion limits differ across CI environments.
            # Force decoder failure to exercise the exact sanitized boundary.
            with patch.object(cockpit.json, "loads",
                              side_effect=RecursionError("untrusted depth")):
                with self.assertRaisesRegex(cockpit.EvidenceError,
                                            "^invalid_api_json$"):
                    cockpit.GitHubReadOnly().get(REF)

    def test_nonfinite_json_denied(self):
        with self.assertRaisesRegex(cockpit.EvidenceError, "nonfinite"):
            json.loads('{"id": NaN}', parse_constant=cockpit.nonfinite)

    def test_html_output_encodes_remote_text(self):
        data = cockpit.collect(FakeAPI())
        data["warnings"] = ['<script>alert("x")</script>']
        page = cockpit.render_html(data)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Bevoegd tot deploy: <strong>NEE</strong>", page)

    def test_html_contains_only_safe_canonical_source_links(self):
        data = cockpit.collect(FakeAPI())
        page = cockpit.render_html(data)
        self.assertIn("https://github.com/Zennay/zCloud/commit/" + SHA, page)
        self.assertIn("https://github.com/Zennay/zCloud/actions/runs/92", page)
        self.assertIn("Bronnen (alleen lezen)", page)
        self.assertFalse(data["release_authorized"])

    def test_html_rejects_untrusted_ids_for_source_navigation(self):
        data = cockpit.collect(FakeAPI())
        data["main_sha"] = "<script>alert(1)</script>"
        data["deploy"]["run_id"] = "javascript:alert(1)"
        page = cockpit.render_html(data)
        self.assertIn("Geen geverifieerde bronlink", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn("https://github.com/Zennay/zCloud/actions/runs/javascript", page)

    def test_html_never_uses_boolean_run_id_as_link(self):
        data = cockpit.collect(FakeAPI())
        data["deploy"]["run_id"] = True
        page = cockpit.render_html(data)
        self.assertNotIn("actions/runs/True", page)

    def test_incomplete_observation_keeps_artifact_before_red_ci(self):
        workflow = (SOURCE.parents[1] / ".github/workflows" /
                    "zcloud-release-evidence-cockpit-20261015.yml").read_text()
        self.assertIn("id: observe", workflow)
        self.assertIn("0|1|2) printf 'evidence_rc=%s", workflow)
        self.assertIn("steps.observe.outputs.evidence_rc == '2'", workflow)
        upload = workflow.index("Retain sanitized offline HTML and JSON for 1 day")
        deny = workflow.index("Fail closed AFTER artifact for incomplete GitHub evidence")
        self.assertLess(upload, deny)
        self.assertIn("exit 2", workflow[deny:])
        self.assertIn("if-no-files-found: error", workflow[upload:deny])

    def test_hosted_observer_shell_retains_diagnostics_then_denies(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path

        workflow = (SOURCE.parents[1] / ".github/workflows" /
                    "zcloud-release-evidence-cockpit-20261015.yml").read_text()
        collect = workflow.split("        id: observe\n", 1)[1]
        shell = collect.split("        run: |\n", 1)[1].split("      - name:", 1)[0]
        commands = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in shell.splitlines()
        )
        for rc in (0, 1, 2):
            with self.subTest(rc=rc), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                binary = root / "bin"
                binary.mkdir()
                fake_python = binary / "python3"
                fake_python.write_text(
                    "#!/bin/sh\n"
                    "printf '{\"release_authorized\": false}' > deploy-ops-evidence.json\n"
                    "printf '<html>Geen toestemming</html>' > deploy-ops-evidence.html\n"
                    f"exit {rc}\n"
                )
                fake_python.chmod(0o755)
                summary = root / "summary"
                output = root / "outputs"
                env = dict(os.environ, PATH=str(binary) + ":" + os.environ["PATH"],
                           GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
                completed = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", commands], cwd=root,
                    env=env, capture_output=True, text=True, check=False
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(f"evidence_rc={rc}", output.read_text())
                self.assertIn("NO RELEASE AUTHORITY", summary.read_text())
                self.assertTrue((root / "deploy-ops-evidence.json").is_file())
                self.assertTrue((root / "deploy-ops-evidence.html").is_file())
                if rc == 2:
                    deny = workflow.split(
                        "Fail closed AFTER artifact for incomplete GitHub evidence", 1
                    )[1].split("        run: |\n", 1)[1]
                    denied = subprocess.run(
                        ["bash", "-e", "-c", deny], cwd=root,
                        capture_output=True, text=True, check=False
                    )
                    self.assertEqual(denied.returncode, 2)

    def test_observer_still_has_no_self_hosted_or_write_permissions(self):
        workflow = (SOURCE.parents[1] / ".github/workflows" /
                    "zcloud-release-evidence-cockpit-20261015.yml").read_text()
        self.assertGreaterEqual(workflow.count("runs-on: ubuntu-latest"), 2)
        self.assertNotIn("runs-on: self-hosted", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("actions: write", workflow)
        self.assertNotIn("statuses: write", workflow)
        self.assertIn("retention-days: 1", workflow)

    def test_dutch_operator_warnings_without_losing_json_codes(self):
        api = FakeAPI()
        api.pages[STAT] = [[]]
        api.pages[RUNS] = [{"workflow_runs": []}]
        data = cockpit.collect(api)
        self.assertIn("production_not_success", data["warnings"])
        page = cockpit.render_html(data)
        self.assertIn("Geen bevestigde geslaagde productiestatus", page)
        self.assertNotIn("<li>production_not_success</li>", page)
        self.assertIn("VPS-job is niet aantoonbaar geslaagd", page)
        self.assertFalse(data["deployment_authorized"])

    def test_incomplete_inventory_is_explained_in_dutch(self):
        data = cockpit.collect(FakeAPI())
        data["warnings"] = ["deploy_run_inventory_incomplete"]
        data["deploy"]["run_inventory_complete"] = False
        page = cockpit.render_html(data)
        self.assertIn("Deploygeschiedenis is onvolledig", page)
        self.assertIn("volledige historie: NEE (gedeeltelijk)", page)
        self.assertFalse(data["release_authorized"])

    def test_next_checks_offer_read_only_steps_for_missing_proof(self):
        api = FakeAPI()
        api.pages[STAT] = [[]]
        api.pages[RUNS] = [{"workflow_runs": []}]
        data = cockpit.collect(api)
        self.assertTrue(data["next_checks"])
        self.assertTrue(any("lees" in x or "Controleer" in x
                            or "Ververs" in x or "Bekijk" in x
                            for x in data["next_checks"]))
        self.assertFalse(data["release_authorized"])
        self.assertIn("Veilige volgende controles", cockpit.render_html(data))

    def test_next_checks_have_no_runtime_mutation_authority(self):
        guidance = cockpit.operator_next_checks([
            "production_not_success", "runner_route_not_proven",
            "deploy_observation_stale_or_missing", "deploy_run_not_success"
        ])
        self.assertEqual(len(guidance), 4)
        self.assertIn("herstart of herbewapen niets", guidance[0])
        self.assertTrue(all(isinstance(x, str) for x in guidance))
        self.assertNotIn("deployment_authorized", " ".join(guidance))

    def test_no_warnings_still_requires_independent_release_review(self):
        guidance = cockpit.operator_next_checks([])
        self.assertEqual(len(guidance), 1)
        self.assertIn("geen deploygoedkeuring", guidance[0])
        self.assertFalse(cockpit.collect(FakeAPI())["deployment_authorized"])

    def test_old_green_and_missing_deploy_cannot_grant_permission(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": []}]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["state"], "success")
        self.assertFalse(data["deployment_authorized"])
        self.assertEqual(data["deploy"]["status"], "not_observed")


if __name__ == "__main__":
    unittest.main()
