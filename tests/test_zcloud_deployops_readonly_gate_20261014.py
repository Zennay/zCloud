"""Offline, non-privileged tests for bounded zCloud production gate observations."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deployops_readonly_gate_20261014.py"
spec = importlib.util.spec_from_file_location("gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SHA = "a" * 40
OTHER_SHA = "b" * 40


def fixture():
    rid = 1234
    return {
        "repo": gate.REPO,
        "main_sha": SHA,
        "main_sha_after": SHA,
        "status": {
            "sha": SHA,
            "statuses": [
                {"id": 2, "context": "zcloud/vps-production", "state": "failure",
                 "created_at": "2026-10-14T04:30:00Z"},
                {"id": 1, "context": "zcloud/vps-production", "state": "success",
                 "created_at": "2026-10-14T03:00:00Z"}
            ],
        },
        "runs": {"total_count": 1, "workflow_runs": [
            {"id": rid, "run_attempt": 1, "head_sha": SHA, "head_branch": "main",
             "path": gate.WORKFLOW, "created_at": "2026-10-14T04:31:00Z",
             "status": "completed", "conclusion": "failure"}
        ]},
        "jobs_by_run": {str(rid): {
            "total_count": 1,
            "jobs": [{"id": 9, "run_id": rid, "status": "completed", "conclusion": "failure",
                      "runner_id": 21, "runner_name": "zcloud-vps-1"}]
        }},
    }


class GateTests(unittest.TestCase):
    def test_current_main_failure_is_not_runner_outage(self):
        result = gate.classify(fixture())
        self.assertEqual(result["observation"], "OBSERVED_MAIN_DEPLOY_FAILURE")
        self.assertEqual(result["production_status"], "failure")
        self.assertEqual(result["latest_run_failed_jobs"], 1)
        self.assertFalse(result["production_verified"])
        self.assertFalse(result["deploy_history_exhaustive"])
        self.assertFalse(result["mutation_authorized"])
        self.assertFalse(result["runner_outage_verified"])

    def test_latest_production_status_wins_not_first(self):
        item = fixture()
        item["status"]["statuses"][0]["created_at"] = "2026-10-14T03:00:00Z"
        item["status"]["statuses"][1]["created_at"] = "2026-10-14T04:30:00Z"
        result = gate.classify(item)
        self.assertEqual(result["production_status"], "success")
        self.assertFalse(result["production_verified"])

    def test_green_status_never_makes_release_authorized(self):
        item = fixture()
        item["status"]["statuses"][0]["state"] = "success"
        item["runs"]["workflow_runs"][0]["conclusion"] = "success"
        item["jobs_by_run"]["1234"]["jobs"][0]["conclusion"] = "success"
        result = gate.classify(item)
        self.assertEqual(result["observation"], "STATUS_GREEN_UNVERIFIED")
        self.assertFalse(result["release_authorized"])

    def test_queued_unassigned_is_not_listener_outage_proof(self):
        item = fixture()
        item["runs"]["workflow_runs"][0].update(status="queued", conclusion=None)
        item["jobs_by_run"]["1234"]["jobs"][0].update(
            status="queued", conclusion=None, runner_id=None, runner_name=None)
        result = gate.classify(item)
        self.assertEqual(result["observation"], "DEPLOY_JOB_QUEUED_UNASSIGNED")
        self.assertEqual(result["latest_run_queued_unassigned_jobs"], 1)
        self.assertFalse(result["runner_outage_verified"])

    def test_old_branch_or_old_sha_not_used_as_main_success(self):
        item = fixture()
        item["runs"]["workflow_runs"][0]["head_sha"] = OTHER_SHA
        self.assertEqual(gate.classify(item)["current_main_deploy_run"], None)

    def test_absent_status_denies_healthy_claim_not_observation(self):
        item = fixture()
        item["status"]["statuses"] = []
        self.assertEqual(gate.classify(item)["production_status"], "absent")

    def test_no_runs_is_allowed_without_forging_deploy(self):
        item = fixture()
        item["runs"] = {"total_count": 0, "workflow_runs": []}
        item["jobs_by_run"] = {}
        self.assertIsNone(gate.classify(item)["current_main_deploy_run"])

    def test_broken_provenance_deny(self):
        alterations = [
            lambda x: x.update(repo="Attacker/zCloud"),
            lambda x: x.update(main_sha_after=OTHER_SHA),
            lambda x: x["status"].update(sha=OTHER_SHA),
            lambda x: x["runs"].update(total_count=2),
            lambda x: x["runs"]["workflow_runs"][0].update(path="attacker.yml"),
            lambda x: x["runs"]["workflow_runs"][0].update(head_branch="stale"),
            lambda x: x["runs"]["workflow_runs"][0].update(run_attempt=True),
            lambda x: x["runs"]["workflow_runs"][0].update(status="nope"),
            lambda x: x["jobs_by_run"]["1234"]["jobs"][0].update(run_id=4321),
            lambda x: x["jobs_by_run"]["1234"]["jobs"][0].update(id=True),
            lambda x: x["jobs_by_run"]["1234"]["jobs"][0].update(runner_id=True),
            lambda x: x["jobs_by_run"]["1234"]["jobs"][0].update(runner_id=21, runner_name=None),
            lambda x: x["jobs_by_run"]["1234"]["jobs"][0].update(runner_id=None, runner_name="fake-runner"),
            lambda x: x["status"]["statuses"][0].update(id=1),
            lambda x: x["status"]["statuses"][0].update(created_at="2026-10-14"),
            lambda x: x["status"]["statuses"][0].update(state="greenish"),
            lambda x: x["jobs_by_run"].update({"extra": {"total_count": 0, "jobs": []}}),
        ]
        for change in alterations:
            with self.subTest(change=change):
                item = fixture()
                change(item)
                result = gate.safe_report(item)
                self.assertEqual(result["observation"], "DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE")
                self.assertFalse(result["mutation_authorized"])
                self.assertNotIn("main_sha", result)

    def test_extra_and_duplicate_run_entries_deny(self):
        item = fixture()
        item["runs"]["workflow_runs"].append(copy.deepcopy(item["runs"]["workflow_runs"][0]))
        item["runs"]["total_count"] = 2
        self.assertEqual(gate.safe_report(item)["observation"], "DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE")

    def test_noncanonical_json_and_unicode_rejected(self):
        bad = ['{"k":1,"k":2}', '{"x": NaN}', '{"x":Infinity}', "[]"]
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(gate.EvidenceError):
                    gate.load_json(raw)

    def test_readonly_api_endpoint_allowlist_and_crlf_token(self):
        # A denied URL or credential never reaches urllib.request.urlopen.
        for path, token in [
            ("/actions/runs/123/cancel", "safe"),
            ("/issues", "safe"),
            ("/actions/runs/1/jobs?per_page=100#evil", "safe"),
            ("/branches/main", "a\nb"),
            ("/branches/main", "a b"),
            ("/branches/main", "🦄"),
        ]:
            with self.subTest(path=path, token=token):
                with self.assertRaises(gate.EvidenceError):
                    gate.api(path, token)

    def test_live_snapshot_binds_main_before_after(self):
        original = fixture()
        calls = []

        def mocked_api(path, token):
            calls.append((path, token))
            if path == "/branches/main":
                return {"commit": {"sha": SHA if len(calls) == 1 else OTHER_SHA}}
            if path == "/commits/main/status":
                return original["status"]
            if path.startswith("/actions/workflows/"):
                return original["runs"]
            if path == "/actions/runs/1234/jobs?per_page=100":
                return original["jobs_by_run"]["1234"]
            self.fail("unexpected GitHub route")

        with mock.patch.object(gate, "api", side_effect=mocked_api):
            seen = gate.live_snapshot("safe")
        self.assertEqual(len(calls), 5)
        self.assertEqual(seen["main_sha"], SHA)
        self.assertEqual(seen["main_sha_after"], OTHER_SHA)
        self.assertEqual(gate.safe_report(seen)["observation"], "DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE")

    def test_live_transport_is_get_only(self):
        body = b'{"commit":{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        with mock.patch.object(gate.urllib.request, "urlopen", return_value=response) as urlopen:
            result = gate.api("/branches/main", "ghp_test1")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.endswith("/branches/main"))
        self.assertEqual(result["commit"]["sha"], SHA)

    def test_completed_failure_without_job_is_not_runner_outage(self):
        item = fixture()
        item["jobs_by_run"]["1234"] = {"total_count": 0, "jobs": []}
        result = gate.classify(item)
        self.assertEqual(result["observation"], "OBSERVED_MAIN_DEPLOY_FAILURE")
        self.assertEqual(result["latest_run_failed_jobs"], 0)
        self.assertFalse(result["runner_outage_verified"])

    def test_no_job_leak_on_failure(self):
        item = fixture()
        item["jobs_by_run"]["1234"]["jobs"][0]["runner_name"] = "INTERNAL_NAME_FORBIDDEN"
        content = json.dumps(gate.safe_report(item))
        self.assertNotIn("INTERNAL_NAME_FORBIDDEN", content)


if __name__ == "__main__":
    unittest.main()
