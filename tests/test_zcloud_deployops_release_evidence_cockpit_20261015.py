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
RUNS = ("/repos/Zennay/zCloud/actions/workflows/zcloud-vps-deploy.yml/runs"
        "?branch=main&per_page=100&page=1")
JOBS = "/repos/Zennay/zCloud/actions/runs/92/jobs?per_page=100&page=1"
RUN_DETAIL = "/repos/Zennay/zCloud/actions/runs/92"


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
        with self.assertRaisesRegex(cockpit.EvidenceError, "duplicate_deploy_run_identity"):
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
