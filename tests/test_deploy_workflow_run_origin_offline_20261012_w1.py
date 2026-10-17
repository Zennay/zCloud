"""Deterministic, network-free deployment trust-boundary regression matrix."""
from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.zcloud_deploy_workflow_run_origin_offline_20261012_w1 import (
    classify, classify_github_payload, Decision, REPOSITORY, REPOSITORY_ID,
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


def actual_github_payload():
    fixture = accepted_fixture()
    run = deepcopy(fixture["workflow_run"])
    run["repository"] = {"full_name": REPOSITORY, "id": REPOSITORY_ID}
    run["head_repository"]["id"] = REPOSITORY_ID
    return {
        "action": "completed",
        "repository": {"full_name": REPOSITORY, "id": REPOSITORY_ID, "private": True},
        "workflow_run": run,
        "sender": {"login": "not-an-authorization"},
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

    def test_real_webhook_object_is_observational_only(self):
        result = classify_github_payload(
            actual_github_payload(), event_name="workflow_run", canonical_main_sha=MAIN,
        )
        self.assertTrue(result.trusted_origin_observed)
        self.assertEqual(result.reason, "exact_main_origin_observed")
        self.assertFalse(any((result.actions_cancel_authorized, result.runner_restart_authorized,
                              result.worker_push_authorized, result.deploy_authorized)))

    def test_real_webhook_rejects_wrong_outer_event_name(self):
        for invalid in (None, True, "push", "workflow_dispatch",
                        "pull_request", "workflow_run "):
            with self.subTest(event=repr(invalid)):
                result = classify_github_payload(
                    actual_github_payload(), event_name=invalid, canonical_main_sha=MAIN,
                )
                self.assertEqual(result.reason, "event_not_workflow_run")
                self.assertFalse(result.trusted_origin_observed)
                self.assertFalse(result.deploy_authorized)

    def test_actual_webhook_requires_positive_exact_event_repo_id(self):
        for invalid in (None, True, False, 0, -1, float(REPOSITORY_ID), str(REPOSITORY_ID), [], {}):
            with self.subTest(id=repr(invalid)):
                payload = actual_github_payload()
                payload["repository"]["id"] = invalid
                decision = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(decision.reason, "invalid_event_repository_id")
                self.assertFalse(decision.trusted_origin_observed)
                self.assertFalse(decision.deploy_authorized)

    def test_matching_wrong_repository_ids_are_still_untrusted(self):
        for invalid in (1, REPOSITORY_ID - 1, REPOSITORY_ID + 1, 999999999):
            with self.subTest(id=invalid):
                payload = actual_github_payload()
                payload["repository"]["id"] = invalid
                payload["workflow_run"]["repository"]["id"] = invalid
                payload["workflow_run"]["head_repository"]["id"] = invalid
                decision = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(decision.reason, "event_repository_id_mismatch")
                self.assertFalse(decision.trusted_origin_observed)
                self.assertFalse(decision.actions_cancel_authorized)
                self.assertFalse(decision.runner_restart_authorized)
                self.assertFalse(decision.worker_push_authorized)
                self.assertFalse(decision.deploy_authorized)

    def test_verified_canonical_repo_id_is_exact_immutable_constant(self):
        self.assertEqual(REPOSITORY_ID, 1389227570)
        payload = actual_github_payload()
        decision = classify_github_payload(
            payload, event_name="workflow_run", canonical_main_sha=MAIN,
        )
        self.assertTrue(decision.trusted_origin_observed)
        self.assertFalse(decision.deploy_authorized)

    def test_actual_webhook_rejects_malformed_or_forked_run_repository(self):
        for invalid in (None, [], {}, "Zennay/zCloud",
                        {"full_name": "fork/zCloud", "id": REPOSITORY_ID},
                        {"full_name": REPOSITORY + " ", "id": REPOSITORY_ID}):
            with self.subTest(repo=repr(invalid)):
                payload = actual_github_payload()
                payload["workflow_run"]["repository"] = invalid
                decision = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(decision.reason, "untrusted_run_repository")
                self.assertFalse(decision.trusted_origin_observed)
                self.assertFalse(decision.actions_cancel_authorized)

    def test_actual_webhook_run_repo_id_must_match_event_repo_id(self):
        for invalid in (None, True, 0, 99, float(REPOSITORY_ID), str(REPOSITORY_ID)):
            with self.subTest(id=repr(invalid)):
                payload = actual_github_payload()
                payload["workflow_run"]["repository"]["id"] = invalid
                decision = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(decision.reason, "run_repository_id_mismatch")
                self.assertFalse(decision.trusted_origin_observed)
                self.assertFalse(decision.runner_restart_authorized)

    def test_actual_webhook_head_repo_id_must_match_event_repo_id(self):
        for invalid in (None, True, 0, 99, float(REPOSITORY_ID), str(REPOSITORY_ID)):
            with self.subTest(id=repr(invalid)):
                payload = actual_github_payload()
                payload["workflow_run"]["head_repository"]["id"] = invalid
                decision = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(decision.reason, "head_repository_id_mismatch")
                self.assertFalse(decision.trusted_origin_observed)
                self.assertFalse(decision.worker_push_authorized)

    def test_real_webhook_requires_completed_outer_action(self):
        for invalid in (None, True, "requested", "in_progress", "completed "):
            payload = actual_github_payload()
            payload["action"] = invalid
            with self.subTest(action=repr(invalid)):
                result = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(result.reason, "source_action_not_completed")
                self.assertFalse(result.trusted_origin_observed)

    def test_real_webhook_requires_exact_repository_object(self):
        for repo in (None, "Zennay/zCloud", [], {}, {"full_name": "Other/zCloud"},
                     {"full_name": REPOSITORY + " "}, {"full_name": None}):
            payload = actual_github_payload()
            payload["repository"] = repo
            with self.subTest(repository=repr(repo)):
                result = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(result.reason, "trigger_repository_mismatch")
                self.assertFalse(result.worker_push_authorized)

    def test_real_webhook_rejects_forked_and_stale_inner_run(self):
        for field, invalid, reason in (
            ("head_repository", {"full_name": "fork/zCloud"}, "untrusted_source_repository"),
            ("head_sha", SECOND, "stale_or_invalid_source_sha"),
            ("conclusion", "failure", "source_not_successful_terminal"),
            ("workflow_id", 1, "wrong_workflow_id"),
            ("event", "pull_request", "untrusted_source_event"),
        ):
            payload = actual_github_payload()
            payload["workflow_run"][field] = invalid
            with self.subTest(field=field):
                result = classify_github_payload(
                    payload, event_name="workflow_run", canonical_main_sha=MAIN,
                )
                self.assertEqual(result.reason, reason)
                self.assertFalse(result.trusted_origin_observed)

    def test_real_webhook_rejects_malformed_envelope(self):
        for payload in (None, [], "raw-json", {}, {"repository": REPOSITORY}):
            result = classify_github_payload(
                payload, event_name="workflow_run", canonical_main_sha=MAIN,
            )
            self.assertFalse(result.trusted_origin_observed)
            self.assertFalse(result.actions_cancel_authorized)

    def test_real_webhook_does_not_mutate_input_or_write_state(self):
        payload = actual_github_payload()
        original = deepcopy(payload)
        first = classify_github_payload(
            payload, event_name="workflow_run", canonical_main_sha=MAIN,
        )
        second = classify_github_payload(
            payload, event_name="workflow_run", canonical_main_sha=MAIN,
        )
        self.assertEqual(first, second)
        self.assertEqual(payload, original)
        self.assertTrue(first.trusted_origin_observed)
        self.assertFalse(first.deploy_authorized)

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
