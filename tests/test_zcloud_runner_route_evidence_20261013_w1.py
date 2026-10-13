#!/usr/bin/env python3
"""Offline denial/positive routing matrix; no API credentials or permanent runner."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/zcloud_runner_route_evidence_20261013_w1.py"
spec = importlib.util.spec_from_file_location("route", SOURCE)
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)
NOW = datetime(2026, 10, 13, 21, 50, tzinfo=timezone.utc)


def make_job(*, job_id=12, labels=None, status="queued", runner_id=0,
             runner_name="", conclusion=None, created_at="2026-10-13T21:40:00Z"):
    if labels is None:
        labels = ["self-hosted", "zcloud", "vps"]
    return {"id": job_id, "labels": labels, "status": status,
            "runner_id": runner_id, "runner_name": runner_name,
            "conclusion": conclusion, "created_at": created_at}


def make_deploy_run(run_id=111, created="2026-10-13T21:35:00Z"):
    repo = {"id": route.CANONICAL_REPOSITORY_ID, "full_name": route.REPO}
    return {
        "id": run_id, "name": "zCloud VPS deploy", "head_branch": "main",
        "head_repository": dict(repo), "repository": dict(repo),
        "workflow_id": route.CANONICAL_DEPLOY_WORKFLOW_ID,
        "path": route.CANONICAL_DEPLOY_WORKFLOW_PATH,
        "event": "workflow_run", "run_attempt": 1,
        "head_sha": "a" * 40, "created_at": created,
    }


class RunnerRouteEvidenceTests(unittest.TestCase):
    def test_unassigned_queue_is_not_false_green(self):
        data = route.classify_run(1, [make_job()], NOW)
        self.assertEqual(data["lanes"]["zcloud_vps"]["unassigned_count"], 1)
        self.assertEqual(data["jobs"][0]["queue_age_seconds"], 600)
        self.assertFalse(data["production_deploy_verified"])
        self.assertFalse(data["authorizes_mutation"])

    def test_completed_failed_deploy_is_assigned_but_not_prod_green(self):
        x = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                     conclusion="failure")
        data = route.classify_run(2, [x], NOW)
        self.assertEqual(data["lanes"]["zcloud_vps"]["assigned_count"], 1)
        self.assertEqual(data["lanes"]["zcloud_vps"]["failed_jobs"], 1)
        self.assertFalse(data["production_deploy_verified"])

    def test_cpu_snapshot_proves_runner_route(self):
        x = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                     conclusion="success")
        data = route.classify_run(3, [x], NOW)
        self.assertEqual(data["lanes"]["zcloud_vps"]["assigned_count"], 1)
        self.assertFalse(data["production_deploy_verified"])

    def test_different_ftmo_lane_must_not_close_zcloud_route(self):
        x = make_job(labels=["self-hosted", "ftmo-research"])
        data = route.classify_run(4, [x], NOW)
        self.assertEqual(data["lanes"]["ftmo_research"]["unassigned_count"], 1)
        self.assertEqual(data["lanes"]["zcloud_vps"]["job_count"], 0)

    def test_hosted_runner_does_not_establish_self_hosted_proof(self):
        x = make_job(labels=["ubuntu-latest"], status="completed",
                     runner_id=999, runner_name="GitHub Actions 999", conclusion="success")
        data = route.classify_run(5, [x], NOW)
        self.assertEqual(data["lanes"]["zcloud_vps"]["assigned_count"], 0)

    def test_mixed_lanes_denied(self):
        x = make_job(labels=["self-hosted", "ftmo-research", "zcloud", "vps"])
        with self.assertRaises(route.InvalidEvidence):
            route.classify_run(6, [x], NOW)

    def test_missing_or_partial_identity_denied(self):
        for overrides in ({"runner_id": 21, "runner_name": ""},
                          {"runner_id": 0, "runner_name": "zcloud-vps-1"},
                          {"runner_id": True},
                          {"job_id": True},
                          {"status": "unlisted"}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(route.InvalidEvidence):
                    route.classify_run(7, [make_job(**overrides)], NOW)

    def test_future_time_spoof_denied(self):
        with self.assertRaises(route.InvalidEvidence):
            route.classify_run(8, [make_job(created_at="2026-10-14T20:00:00Z")], NOW)

    def test_unzoned_time_denied(self):
        with self.assertRaises(route.InvalidEvidence):
            route.classify_run(9, [make_job(created_at="2026-10-13T21:39:00")], NOW)

    def test_malformed_conclusion_denied(self):
        for x in (make_job(status="completed", conclusion=None),
                  make_job(status="queued", conclusion="success")):
            with self.assertRaises(route.InvalidEvidence):
                route.classify_run(10, [x], NOW)

    def test_duplicate_job_ids_denied(self):
        with self.assertRaises(route.InvalidEvidence):
            route.classify_run(11, [make_job(), make_job()], NOW)

    def test_empty_batch_denied(self):
        with self.assertRaises(route.InvalidEvidence):
            route.classify_run(12, [], NOW)

    def test_duplicate_json_and_nonfinite_denied(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}',
                    b'{"a":{"b":3,"b":4}}', b'{"a":1' ):
            with self.subTest(raw=raw):
                with self.assertRaises(route.InvalidEvidence):
                    route.strict_json(raw)

    def test_json_size_bound(self):
        with self.assertRaises(route.InvalidEvidence):
            route.strict_json(b"x" * (route.MAX_BYTES + 1))

    def test_cli_fails_closed_without_token(self):
        import contextlib
        import io
        from unittest.mock import patch
        out = io.StringIO()
        with patch.dict("os.environ", {"GH_TOKEN": ""}), contextlib.redirect_stdout(out):
            rc = route.main(["--run-id", "999"])
        self.assertEqual(rc, 2)
        self.assertIn('"authorizes_mutation": false', out.getvalue())
        self.assertNotIn("http", out.getvalue())

    def test_cli_rejects_duplicate_run_selection_without_api(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(route.main(["--run-id", "1", "--run-id", "1"]), 2)
        self.assertIn("invalid_run_selection", out.getvalue())

    def test_latest_deploy_run_snapshot_is_bounded_and_main_only(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        class Headers:
            def get_content_type(self):
                return "application/json"
        class Response:
            headers = Headers()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                data = {"total_count": 3, "workflow_runs": [
                    make_deploy_run(111, created="2026-10-13T21:35:00Z"),
                    make_deploy_run(999, created="2026-10-13T21:30:00Z"),
                    make_deploy_run(222, created="2026-10-13T21:40:00Z")]}
                return json.dumps(data).encode("utf-8")
        class Opener:
            def open(self, request, timeout):
                self.url = request.full_url
                self.timeout = timeout
                return Response()
        opener = Opener()
        with patch.object(route.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(route.latest_main_deploy_ids("fake", 2), [222, 111])
        self.assertTrue(opener.url.startswith("https://api.github.com/repos/Zennay/zCloud/"))
        self.assertEqual(opener.timeout, 15)

    def test_canonical_deployment_source_identity_matrix(self):
        from copy import deepcopy
        baseline = make_deploy_run()
        bad = [
            {"workflow_id": 1}, {"workflow_id": True},
            {"event": "pull_request"}, {"path": ".github/workflows/evil.yml"},
            {"head_branch": "feat/other"}, {"name": "Other workflow"},
            {"head_sha": "a" * 39}, {"head_sha": 2},
            {"id": True}, {"run_attempt": True}, {"run_attempt": 0},
            {"created_at": "2026-10-14T22:00:00Z"},
            {"created_at": "2026-10-13T21:35:00"},
            {"head_repository": {"id": 42, "full_name": route.REPO}},
            {"repository": {"id": route.CANONICAL_REPOSITORY_ID,
                            "full_name": "attacker/clone"}},
            {"repository": {}}, {"head_repository": None},
        ]
        for changed in bad:
            with self.subTest(changed=changed):
                value = deepcopy(baseline)
                value.update(changed)
                with self.assertRaises(route.InvalidEvidence):
                    route._verified_main_deploy_run(value, NOW)
        self.assertEqual(route._verified_main_deploy_run(baseline, NOW),
                         ("2026-10-13T21:35:00Z", 111))

    def test_real_deployment_provenance_matches_canonical_numeric_identity(self):
        self.assertEqual(route.CANONICAL_REPOSITORY_ID, 1389227570)
        self.assertEqual(route.CANONICAL_DEPLOY_WORKFLOW_ID, 369569470)
        self.assertEqual(route.CANONICAL_DEPLOY_WORKFLOW_PATH,
                         ".github/workflows/zcloud-vps-deploy.yml")

    def test_latest_deploy_missing_token_denies_without_api(self):
        with self.assertRaises(route.InvalidEvidence):
            route.latest_main_deploy_ids("")

    def test_latest_deploy_count_bounds(self):
        for count in (0, -1, 4, True):
            with self.subTest(count=count):
                with self.assertRaises(route.InvalidEvidence):
                    route.latest_main_deploy_ids("fake", count)

    def test_cli_no_selection_denied(self):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(route.main([]), 2)
        self.assertIn("invalid_run_selection", output.getvalue())

    def test_safe_idle_failure_identified_as_stage_not_runner_outage(self):
        deploy = make_job(
            status="completed", runner_id=21, runner_name="zcloud-vps-1",
            conclusion="failure")
        deploy["steps"] = [
            {"name": "Check out current main candidate", "conclusion": "success"},
            {"name": "Enter browser safe-idle for guarded promotion",
             "conclusion": "failure"},
            {"name": "Promote backend runtime core", "conclusion": "skipped"}]
        data = route.classify_run(33, [deploy], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"], "browser_safe_idle")
        self.assertEqual(data["lanes"]["zcloud_vps"]["failure_phases"],
                         {"browser_safe_idle": 1})
        self.assertEqual(data["lanes"]["zcloud_vps"]["assigned_count"], 1)
        self.assertFalse(data["production_deploy_verified"])

    def test_distinct_promotion_and_self_heal_failures_are_not_hidden(self):
        deploy = make_job(
            status="completed", runner_id=21, runner_name="zcloud-vps-1",
            conclusion="failure")
        deploy["steps"] = [
            {"name": "Promote backend runtime core", "conclusion": "failure"},
            {"name": "Re-arm VPS-native self-heal after guarded deploy",
             "conclusion": "failure"}]
        data = route.classify_run(34, [deploy], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"], "multiple_failure_phases")

    def test_unknown_step_name_is_redacted_in_output(self):
        secret = "untrusted-step-with-long-sensitive-payload"
        deploy = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                          conclusion="failure")
        deploy["steps"] = [{"name": secret, "conclusion": "failure"}]
        data = route.classify_run(35, [deploy], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"], "other_step")
        self.assertNotIn(secret, str(data))

    def test_failed_job_without_steps_never_becomes_green(self):
        deploy = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                          conclusion="failure")
        data = route.classify_run(36, [deploy], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"], "step_evidence_missing")
        self.assertFalse(data["production_deploy_verified"])

    def test_real_api_empty_steps_on_failed_deploy_means_unverified_phase(self):
        job = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                       conclusion="failure")
        job["steps"] = []
        data = route.classify_run(39, [job], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"], "step_evidence_missing")
        self.assertEqual(data["lanes"]["zcloud_vps"]["failed_jobs"], 1)
        self.assertFalse(data["production_deploy_verified"])

    def test_failed_job_without_failed_step_denies_stage_inference(self):
        job = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                       conclusion="failure")
        job["steps"] = [{"name": "Set up job", "conclusion": "success"}]
        data = route.classify_run(40, [job], NOW)
        self.assertEqual(data["jobs"][0]["failure_phase"],
                         "step_evidence_inconsistent")

    def test_broken_step_types_are_denied(self):
        for steps in ({"name": "fake"}, [1], [{"conclusion": "failure"}],
                      [{"name": "something", "conclusion": {"unexpected": True}}],
                      [{"name": "x" * 201, "conclusion": "failure"}],
                      [{"name": "x", "conclusion": "invented"}],
                      [{"name": "x", "conclusion": "failure"}] * 151):
            deploy = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                              conclusion="failure")
            deploy["steps"] = steps
            with self.subTest(steps=str(steps)[:60]):
                with self.assertRaises(route.InvalidEvidence):
                    route.classify_run(37, [deploy], NOW)

    def test_successful_job_never_reports_failure_phase(self):
        job = make_job(status="completed", runner_id=21, runner_name="zcloud-vps-1",
                       conclusion="success")
        job["steps"] = [{"name": "untrusted", "conclusion": "failure"}]
        data = route.classify_run(38, [job], NOW)
        self.assertIsNone(data["jobs"][0]["failure_phase"])
        self.assertEqual(data["lanes"]["zcloud_vps"]["failure_phases"], {})

    def test_source_contains_no_write_methods_or_prod_authority(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('method="POST"', source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("/cancel", source)
        self.assertNotIn("/dispatches", source)
        self.assertIn('"authorizes_mutation": False', source)


if __name__ == "__main__":
    unittest.main()
