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


def ref(sha=SHA):
    return {"object": {"sha": sha, "type": "commit"}}


def run(sha=SHA, state="completed", conclusion="success"):
    return {"id": 92, "run_attempt": 1, "head_sha": sha, "head_branch": "main",
            "event": "workflow_run", "status": state, "conclusion": conclusion,
            "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def job(status="completed", runner_id=21, runner_name="zcloud-vps-1"):
    return {"labels": ["self-hosted", "zcloud", "vps"], "status": status,
            "runner_id": runner_id, "runner_name": runner_name}


def status(state="success"):
    return [{"context": "zcloud/vps-production", "id": 20,
             "created_at": "2026-10-15T03:00:00Z", "state": state}]


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
            job(), job("queued", 0, None)]}]
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

    def test_old_green_and_missing_deploy_cannot_grant_permission(self):
        api = FakeAPI()
        api.pages[RUNS] = [{"workflow_runs": []}]
        data = cockpit.collect(api)
        self.assertEqual(data["production"]["state"], "success")
        self.assertFalse(data["deployment_authorized"])
        self.assertEqual(data["deploy"]["status"], "not_observed")


if __name__ == "__main__":
    unittest.main()
