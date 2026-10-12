"""Deterministic, network-free deployment trust-boundary regression matrix."""
from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.zcloud_deploy_workflow_run_origin_offline_20261012_w1 import (
    classify, Decision, REPOSITORY,
)

MAIN = "a" * 40
SECOND = "b" * 40


def accepted_fixture():
    return {
        "event_name": "workflow_run",
        "repository": REPOSITORY,
        "workflow_run": {
            "name": "zCloud regression smoke",
            "workflow_id": 367943538,
            "path": ".github/workflows/zcloud-regression-smoke.yml",
            "event": "push",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "status": "completed",
            "conclusion": "success",
            "id": 12493,
            "run_attempt": 2,
            "head_sha": MAIN,
        },
    }


class WorkflowRunOriginOfflineTests(unittest.TestCase):
    def deny(self, event, *, main=MAIN, reason=None):
        result = classify(event, canonical_main_sha=main)
        self.assertIsInstance(result, Decision)
        self.assertFalse(result.trusted_origin_observed, result)
        if reason:
            self.assertEqual(result.reason, reason)
        self.assertFalse(result.actions_cancel_authorized)
        self.assertFalse(result.runner_restart_authorized)
        self.assertFalse(result.worker_push_authorized)
        self.assertFalse(result.deploy_authorized)

    def test_exact_main_success_is_observation_only(self):
        result = classify(accepted_fixture(), canonical_main_sha=MAIN)
        self.assertTrue(result.trusted_origin_observed)
        self.assertEqual(result.reason, "exact_main_origin_observed")
        self.assertFalse(result.actions_cancel_authorized)
        self.assertFalse(result.runner_restart_authorized)
        self.assertFalse(result.worker_push_authorized)
        self.assertFalse(result.deploy_authorized)

    def test_changed_main_invalidates_stale_observation(self):
        self.deny(accepted_fixture(), main=SECOND, reason="stale_or_invalid_source_sha")

    def test_bad_canonical_shas(self):
        for invalid in (None, True, 123, "", "A" * 40, "g" * 40, "a" * 39, "a" * 41, "a" * 40 + "\n"):
            with self.subTest(invalid=repr(invalid)):
                self.deny(accepted_fixture(), main=invalid, reason="canonical_main_unknown")

    def test_invalid_event_shapes(self):
        for invalid in (None, "", True, False, [], (), 1, {"event_name": "workflow_run"}):
            with self.subTest(invalid=repr(invalid)):
                self.deny(invalid)

    def test_event_repository_identity(self):
        for repository in ("Zennay/fork", "evil/zCloud", None, "", {}, ["Zennay/zCloud"]):
            event = accepted_fixture()
            event["repository"] = repository
            with self.subTest(repository=repr(repository)):
                self.deny(event, reason="trigger_repository_mismatch")

    def test_event_types_never_promote(self):
        for value in ("workflow_dispatch", "pull_request", "pull_request_target", "push", "schedule", None):
            event = accepted_fixture()
            event["event_name"] = value
            with self.subTest(value=repr(value)):
                self.deny(event, reason="event_not_workflow_run")

    def test_nested_source_must_be_plain_object(self):
        for value in (None, [], "", 1, True):
            event = accepted_fixture()
            event["workflow_run"] = value
            self.deny(event, reason="source_not_object")

    def test_only_exact_upstream_name(self):
        for value in (None, "zCloud VPS deploy", "zCloud regression smoke ", "Zcloud regression smoke"):
            event = accepted_fixture()
            event["workflow_run"]["name"] = value
            self.deny(event, reason="wrong_workflow")

    def test_upstream_workflow_id_is_exact_integer(self):
        for invalid in (None, True, 0, 367943537, 367943538.0, "367943538"):
            event = accepted_fixture()
            event["workflow_run"]["workflow_id"] = invalid
            with self.subTest(value=repr(invalid)):
                self.deny(event, reason="wrong_workflow_id")

    def test_upstream_workflow_file_identity_is_exact(self):
        for invalid in (None, "", ".github/workflows/other.yml",
                        ".github/workflows/zcloud-regression-smoke.yml@refs/heads/main",
                        ".github/workflows/Zcloud-regression-smoke.yml"):
            event = accepted_fixture()
            event["workflow_run"]["path"] = invalid
            with self.subTest(value=repr(invalid)):
                self.deny(event, reason="wrong_workflow_path")

    def test_source_must_be_push_not_pr_or_manual(self):
        for value in ("pull_request", "pull_request_target", "workflow_dispatch", "schedule", None):
            event = accepted_fixture()
            event["workflow_run"]["event"] = value
            self.deny(event, reason="untrusted_source_event")

    def test_source_branch_must_be_exact_main(self):
        for value in ("worker/tmp", "main ", "refs/heads/main", None, "", "MAIN"):
            event = accepted_fixture()
            event["workflow_run"]["head_branch"] = value
            self.deny(event, reason="untrusted_source_branch")

    def test_source_repo_forks_and_malformed_shapes_denied(self):
        for repo in (None, {}, {"full_name": "Other/zCloud"}, "Zennay/zCloud", [], {"full_name": None}):
            event = accepted_fixture()
            event["workflow_run"]["head_repository"] = repo
            self.deny(event, reason="untrusted_source_repository")

    def test_source_must_be_terminal_success(self):
        for status, conclusion in (
            ("queued", None), ("in_progress", None), ("completed", "failure"),
            ("completed", "cancelled"), ("completed", "skipped"),
            ("completed", "timed_out"), ("completed", "neutral"),
            ("completed", None), ("completed", True),
            (None, "success"), ("in_progress", "success"),
        ):
            event = accepted_fixture()
            event["workflow_run"].update(status=status, conclusion=conclusion)
            with self.subTest(status=status, conclusion=conclusion):
                self.deny(event, reason="source_not_successful_terminal")

    def test_run_id_is_positive_exact_integer(self):
        for invalid in (None, 0, -1, True, 1.0, "1", {}, []):
            event = accepted_fixture()
            event["workflow_run"]["id"] = invalid
            self.deny(event, reason="invalid_source_run_id")

    def test_run_attempt_is_bounded_exact_integer(self):
        for invalid in (None, 0, -1, True, 2.0, "2", {}, [], 100001):
            event = accepted_fixture()
            event["workflow_run"]["run_attempt"] = invalid
            self.deny(event, reason="invalid_source_attempt")

    def test_head_sha_must_be_exact_lowercase_main(self):
        for value in (None, True, "", "A" * 40, "z" * 40, "a" * 39, "a" * 41, SECOND, MAIN + "\n"):
            event = accepted_fixture()
            event["workflow_run"]["head_sha"] = value
            self.deny(event, reason="stale_or_invalid_source_sha")

    def test_source_mutation_does_not_change_earlier_verdict(self):
        source = accepted_fixture()
        decision = classify(source, canonical_main_sha=MAIN)
        self.assertTrue(decision.trusted_origin_observed)
        source["workflow_run"]["head_sha"] = SECOND
        self.assertFalse(classify(source, canonical_main_sha=MAIN).trusted_origin_observed)
        self.assertTrue(decision.trusted_origin_observed)
        self.assertFalse(decision.runner_restart_authorized)

    def test_no_filesystem_network_or_service_side_effect(self):
        # Public helper accepts only in-memory event maps, imports no side-effect
        # library, and never authorizes any effect even for accepted evidence.
        event = deepcopy(accepted_fixture())
        for _ in range(3):
            result = classify(event, canonical_main_sha=MAIN)
            self.assertTrue(result.trusted_origin_observed)
            self.assertFalse(any((
                result.actions_cancel_authorized,
                result.runner_restart_authorized,
                result.worker_push_authorized,
                result.deploy_authorized,
            )))
        self.assertEqual(event, accepted_fixture())


if __name__ == "__main__":
    unittest.main()
