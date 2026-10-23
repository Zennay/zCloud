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
    def test_skipped_and_approval_waits_are_not_runner_outages(self):
        jobs = [
            make_job(job_id=101, status="waiting"),
            make_job(job_id=102, status="pending"),
            make_job(job_id=103, status="requested"),
            make_job(job_id=104, status="completed", conclusion="skipped"),
            make_job(job_id=105, status="queued"),
        ]
        report = route.classify_run(34, jobs, NOW)
        lane = report["lanes"]["zcloud_vps"]
        self.assertEqual(lane["job_count"], 5)
        self.assertEqual(lane["not_yet_scheduled_count"], 3)
        self.assertEqual(lane["not_dispatched_count"], 1)
        self.assertEqual(lane["unassigned_count"], 1)
        self.assertEqual(lane["max_unassigned_seconds"], 600)
        self.assertEqual(lane["assigned_count"], 0)
        self.assertFalse(report["authorizes_mutation"])
        self.assertEqual([j["route"] for j in report["jobs"]],
                         ["not_yet_scheduled"] * 3 + ["not_dispatched", "unassigned"])

    def test_completed_failure_with_missing_runner_is_unknown_not_queued(self):
        data = route.classify_run(44, [
            make_job(status="completed", conclusion="failure")], NOW)
        self.assertEqual(data["lanes"]["zcloud_vps"]["unassigned_count"], 0)
        self.assertEqual(data["lanes"]["zcloud_vps"]["unproven_runner_count"], 1)
        self.assertFalse(data["production_deploy_verified"])

    def test_skipped_job_cannot_claim_a_running_vps(self):
        record = make_job(status="completed", conclusion="skipped",
                          runner_id=21, runner_name="zcloud-vps-1")
        with self.assertRaisesRegex(route.InvalidEvidence, "skipped_job_claims_runner"):
            route.classify_job(record, NOW)

    def test_terminal_non_success_is_not_hidden_by_failure_only_metric(self):
        inputs = [
            make_job(job_id=1, status="completed", conclusion="cancelled"),
            make_job(job_id=2, status="completed", conclusion="timed_out"),
            make_job(job_id=3, status="completed", conclusion="skipped"),
            make_job(job_id=4, status="completed", conclusion="success",
                     runner_id=21, runner_name="zcloud-vps-1"),
        ]
        lane = route.classify_run(19, inputs, NOW)["lanes"]["zcloud_vps"]
        self.assertEqual(lane["failed_jobs"], 0)
        self.assertEqual(lane["non_successful_terminal_count"], 2)
        self.assertEqual(lane["not_dispatched_count"], 1)

    def test_arbitrary_terminal_conclusions_fail_closed(self):
        for bad in ("passed", "unknown", {}, [], True, 0):
            with self.subTest(conclusion=bad):
                record = make_job(status="completed", conclusion=bad)
                with self.assertRaisesRegex(route.InvalidEvidence, "invalid_conclusion"):
                    route.classify_job(record, NOW)

    def test_unhashable_status_evidence_fails_closed(self):
        for bad in ([], {}, {"value": "completed"}, True, 1, None):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(route.InvalidEvidence, "invalid_job_status"):
                    route.classify_job(make_job(status=bad), NOW)

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

    def test_matching_labels_on_wrong_vps_are_not_canonical_runner_proof(self):
        zcloud = make_job(job_id=42, status="completed", conclusion="success",
                          runner_id=1234, runner_name="stranger-with-same-labels")
        verified = make_job(job_id=43, status="completed", conclusion="failure",
                            runner_id=21, runner_name="zcloud-vps-1")
        result = route.classify_run(5, [zcloud, verified], NOW)
        self.assertEqual(result["lanes"]["zcloud_vps"]["assigned_count"], 2)
        self.assertEqual(result["lanes"]["zcloud_vps"]["canonical_vps_assigned_count"], 1)
        self.assertEqual(result["lanes"]["zcloud_vps"]["unexpected_vps_assigned_count"], 1)
        self.assertFalse(result["jobs"][0]["canonical_vps_runner"])
        self.assertTrue(result["jobs"][1]["canonical_vps_runner"])
        self.assertFalse(result["production_deploy_verified"])

    def test_matching_runner_name_on_wrong_github_runner_id_is_not_canonical(self):
        # Display name is not unique authority: bind the exact GitHub runner
        # identity recorded for zcloud-vps-1 in real historical job evidence.
        spoof = make_job(job_id=22, status="completed", conclusion="success",
                         runner_id=999, runner_name="zcloud-vps-1")
        real = make_job(job_id=23, status="completed", conclusion="success",
                        runner_id=route.CANONICAL_VPS_RUNNER_ID,
                        runner_name="zcloud-vps-1")
        result = route.classify_run(26, [spoof, real], NOW)
        lane = result["lanes"]["zcloud_vps"]
        self.assertEqual(lane["assigned_count"], 2)
        self.assertEqual(lane["canonical_vps_assigned_count"], 1)
        self.assertEqual(lane["unexpected_vps_assigned_count"], 1)
        self.assertFalse(result["jobs"][0]["canonical_vps_runner"])
        self.assertTrue(result["jobs"][1]["canonical_vps_runner"])
        self.assertFalse(result["production_deploy_verified"])

    def test_ftmo_target_never_inflates_canonical_zcloud_vps_counter(self):
        x = make_job(labels=["self-hosted", "ftmo-research"], status="completed",
                     conclusion="success", runner_id=21, runner_name="zcloud-vps-1")
        value = route.classify_run(6, [x], NOW)
        self.assertEqual(value["lanes"]["zcloud_vps"]["canonical_vps_assigned_count"], 0)
        self.assertEqual(value["lanes"]["ftmo_research"]["canonical_vps_assigned_count"], 0)

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

    def test_private_self_hosted_labels_never_implicate_vps_base_pool(self):
        # A private/custom label on a queued job changes the set of eligible
        # runners. It is not proof of a zcloud/vps listener outage.
        for extra in ("dedicated", "requires-special-hardware", "prod-private"):
            with self.subTest(extra=extra):
                record = make_job(labels=["self-hosted", "zcloud", "vps", extra])
                result = route.classify_run(22, [record], NOW)
                self.assertEqual(result["lanes"]["zcloud_vps"]["unassigned_count"], 0)
                self.assertEqual(result["lanes"]["zcloud_vps"]["job_count"], 0)
                self.assertEqual(result["jobs"][0]["lane"], "other")
                self.assertFalse(result["authorizes_mutation"])

    def test_standard_self_hosted_os_labels_preserve_exact_runner_pool(self):
        for extras in (["Linux", "X64"], ["linux", "arm64"], []):
            with self.subTest(extras=extras):
                zcloud = make_job(labels=["self-hosted", "zcloud", "vps"] + extras)
                ftmo = make_job(job_id=99, labels=["self-hosted", "ftmo-research"] + extras)
                result = route.classify_run(23, [zcloud, ftmo], NOW)
                self.assertEqual(result["lanes"]["zcloud_vps"]["unassigned_count"], 1)
                self.assertEqual(result["lanes"]["ftmo_research"]["unassigned_count"], 1)

    def test_unknown_pool_and_private_ftmo_route_remain_unattributed(self):
        jobs = [
            make_job(job_id=41, labels=["self-hosted", "ftmo-research", "special"]),
            make_job(job_id=42, labels=["self-hosted", "another-runner-pool"]),
        ]
        result = route.classify_run(24, jobs, NOW)
        self.assertEqual(result["lanes"]["ftmo_research"]["job_count"], 0)
        self.assertEqual(result["lanes"]["zcloud_vps"]["job_count"], 0)
        self.assertEqual([x["lane"] for x in result["jobs"]], ["other", "other"])

    def test_duplicate_casefolded_runner_labels_are_untrusted(self):
        for labels in (["self-hosted", "zcloud", "vps", "VPS"],
                       ["self-hosted", "Self-Hosted", "zcloud", "vps"]):
            with self.subTest(labels=labels):
                with self.assertRaisesRegex(route.InvalidEvidence,
                                            "duplicate_runner_label"):
                    route.classify_run(25, [make_job(labels=labels)], NOW)

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

    def test_nonfinite_overflow_and_unicode_surrogate_denied(self):
        examples = [
            b'{"x":1e999}', b'{"x":-1e999}',
            b'{"nested":{"x":"\\ud800"}}',
            b'{"\\udfff":"bad"}',
            b'{"safe":["ok", "\\udbff"]}',
        ]
        for raw in examples:
            with self.subTest(raw=raw):
                with self.assertRaises(route.InvalidEvidence):
                    route.strict_json(raw)

    def test_nested_structure_depth_denied(self):
        payload = b'{"item":' + b'[' * 55 + b'0' + b']' * 55 + b'}'
        with self.assertRaisesRegex(route.InvalidEvidence, "json_structure_limit"):
            route.strict_json(payload)

    def test_valid_unicode_and_finite_values_accepted(self):
        data = route.strict_json('{"x":"café","scores":[0.3,1e-3]}'.encode("utf-8"))
        self.assertEqual(data["x"], "café")
        self.assertEqual(data["scores"], [0.3, 0.001])

    def test_json_size_bound(self):
        with self.assertRaises(route.InvalidEvidence):
            route.strict_json(b"x" * (route.MAX_BYTES + 1))

    def test_malformed_action_tokens_fail_before_any_api_call(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        for token in ("two words", "ghs_token\r\nInjected: x", "\n",
                      "x" * 2049, "invalid/route", "ghs_token:bad"):
            with self.subTest(token_length=len(token)):
                output = io.StringIO()
                with (patch.dict("os.environ", {"GH_TOKEN": token}),
                      patch.object(route.urllib.request, "build_opener",
                                   side_effect=AssertionError("network must not start")),
                      contextlib.redirect_stdout(output)):
                    self.assertEqual(route.main(["--run-id", "42"]), 2)
                    self.assertEqual(route.main(["--recent-deploy"]), 2)
                first, second = output.getvalue().splitlines()
                for line in (first, second):
                    data = json.loads(line)
                    self.assertFalse(data["authorizes_mutation"])
                    self.assertEqual(data["error"], "invalid_actions_token")
                    self.assertNotIn(token, line)

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
            self.assertEqual(route.latest_main_deploy_receipts("fake", 2),
                             [(222, "a" * 40, 1), (111, "a" * 40, 1)])
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

    def test_jobs_api_bind_exact_run_sha_and_workflow_across_pages(self):
        import json
        from unittest.mock import patch

        class Headers:
            def get_content_type(self):
                return "application/json"

        class Response:
            headers = Headers()
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _):
                return json.dumps(self.data).encode("utf-8")

        class Opener:
            def __init__(self, pages):
                self.pages = pages
                self.calls = []
            def open(self, request, timeout):
                self.calls.append((request.full_url, timeout))
                return Response(self.pages[len(self.calls) - 1])

        def receipt(job_id, *, run_id=34, sha="a" * 40, name="zCloud VPS deploy"):
            job = make_job(job_id=job_id, status="completed", runner_id=21,
                           runner_name="zcloud-vps-1", conclusion="success")
            job.update({
                "run_id": run_id, "head_sha": sha, "workflow_name": name,
                "run_attempt": 1,
                "run_url": f"https://api.github.com/repos/{route.REPO}/actions/runs/{run_id}",
                "url": f"https://api.github.com/repos/{route.REPO}/actions/jobs/{job_id}",
                "html_url": f"https://github.com/{route.REPO}/actions/runs/{run_id}/job/{job_id}",
            })
            return job

        good = [{"total_count": 2, "jobs": [receipt(101)]},
                {"total_count": 2, "jobs": [receipt(102)]}]
        opener = Opener(good)
        with patch.object(route.urllib.request, "build_opener", return_value=opener):
            result = route.fetch_jobs(34, "synthetic-test-token")
        self.assertEqual([j["id"] for j in result], [101, 102])
        self.assertIn("/actions/runs/34/jobs?", opener.calls[0][0])
        self.assertEqual([call[1] for call in opener.calls], [15, 15])
        self.assertFalse(route.classify_run(34, result, NOW)["authorizes_mutation"])

        invalid = [
            ([{"total_count": 1, "jobs": [receipt(101, run_id=35)]}],
             "cross_run_job_evidence"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), run_id=True)]}],
             "untrusted_job_provenance"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), head_sha="not-a-sha")]}],
             "untrusted_job_provenance"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), workflow_name="")]}],
             "untrusted_job_provenance"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), run_attempt=True)]}],
             "untrusted_job_provenance"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), run_url=
                "https://api.github.com/repos/attacker/clone/actions/runs/34")]}],
             "untrusted_job_provenance"),
            ([{"total_count": 1, "jobs": [dict(receipt(101), html_url=
                "https://github.com/attacker/clone/actions/runs/34/job/101")]}],
             "untrusted_job_provenance"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 2, "jobs": [dict(receipt(102), run_attempt=2)]}],
             "mixed_run_attempts"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 2, "jobs": [receipt(101)]}],
             "duplicate_job_id"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 3, "jobs": [receipt(102)]}],
             "unstable_pagination"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 2, "jobs": []}],
             "incomplete_pagination"),
            ([{"total_count": True, "jobs": [receipt(101)]}],
             "malformed_pagination"),
            ([{"total_count": 1, "jobs": [receipt(101), receipt(102)]}],
             "incomplete_pagination"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 2, "jobs": [receipt(102, sha="b" * 40)]}],
             "mixed_run_head_sha"),
            ([{"total_count": 2, "jobs": [receipt(101)]},
              {"total_count": 2, "jobs": [receipt(102, name="Other workflow")]}],
             "mixed_run_workflow_name"),
        ]
        for pages, expected in invalid:
            with self.subTest(error=expected):
                opener = Opener(pages)
                with patch.object(route.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(route.InvalidEvidence, expected):
                        route.fetch_jobs(34, "synthetic-test-token")

    def test_job_receipt_matches_observed_github_metadata_contract(self):
        actual = {"run_id": 38471675524, "head_sha":
                  "e5a63e3efb508d8b0a42c148262f9515b5753bed",
                  "id": 115554118242, "run_attempt": 1,
                  "workflow_name": "zCloud VPS deploy",
                  "run_url": "https://api.github.com/repos/Zennay/zCloud/actions/runs/38471675524",
                  "url": "https://api.github.com/repos/Zennay/zCloud/actions/jobs/115554118242",
                  "html_url": "https://github.com/Zennay/zCloud/actions/runs/38471675524/job/115554118242"}
        self.assertEqual(route._verified_job_provenance(actual, 38471675524),
                         (actual["head_sha"], 1))

    def test_recent_deploy_cli_binds_list_head_and_attempt_to_job_receipts(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        job = make_job(status="completed", runner_id=21,
                       runner_name="zcloud-vps-1", conclusion="failure")
        job["head_sha"] = "a" * 40
        job["run_attempt"] = 1
        receipt = [(38471675524, "a" * 40, 1)]
        for altered in ({}, {"head_sha": "b" * 40}, {"run_attempt": 2}):
            with self.subTest(altered=altered):
                output = io.StringIO()
                mutated = dict(job, **altered)
                with (patch.dict("os.environ", {"GH_TOKEN": "fake"}),
                      patch.object(route, "latest_main_deploy_receipts", return_value=receipt),
                      patch.object(route, "fetch_jobs", return_value=[mutated]),
                      contextlib.redirect_stdout(output)):
                    result = route.main(["--recent-deploy"])
                value = json.loads(output.getvalue())
                self.assertFalse(value["authorizes_mutation"])
                if not altered:
                    self.assertEqual(result, 0)
                    self.assertFalse(value["runs"][0]["production_deploy_verified"])
                else:
                    self.assertEqual(result, 2)
                    self.assertEqual(value["error"], "deploy_job_metadata_mismatch")

    def test_explicit_http_auth_and_missing_provenance_is_not_runner_offline(self):
        import contextlib
        import io
        import json
        import urllib.error
        from unittest.mock import patch

        class Opener:
            def __init__(self, code):
                self.code = code
            def open(self, request, timeout):
                raise urllib.error.HTTPError(
                    request.full_url, self.code, "sensitive-server-message", {}, None)

        expected = {401: "github_api_permission_or_limit",
                    403: "github_api_permission_or_limit",
                    404: "github_api_missing_or_inaccessible",
                    429: "github_api_rate_limited",
                    503: "github_api_unavailable"}
        for status_code, reason in expected.items():
            with self.subTest(status_code=status_code):
                for selector in (["--run-id", "42"], ["--recent-deploy"]):
                    output = io.StringIO()
                    with (patch.dict("os.environ", {"GH_TOKEN": "synthetic"}),
                          patch.object(route.urllib.request, "build_opener",
                                       return_value=Opener(status_code)),
                          contextlib.redirect_stdout(output)):
                        self.assertEqual(route.main(selector), 2)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["error"], reason)
                    self.assertFalse(result["authorizes_mutation"])
                    self.assertNotIn("sensitive-server-message", output.getvalue())

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
