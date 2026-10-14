"""Additional offline boundary regressions for recovery issue #1177.

Pure unit tests: no GitHub API, shell, network, production or VPS access.
"""
import dataclasses
import unittest

from scripts.zcloud_dashboard_recovery_offline_gate import RecoveryRequest, evaluate

SHA = "a" * 40


def valid_request():
    return RecoveryRequest(
        event="workflow_dispatch",
        repository="Zennay/zCloud",
        actor_trusted=True,
        ref="refs/heads/main",
        target_sha=SHA,
        canonical_sha=SHA,
        owner_approved=True,
        approval_fresh=True,
        serialized_gates_released=True,
        dashboard_unhealthy=True,
        evidence_complete=True,
        recovery_in_flight=False,
        request_id="incident_1177",
    )


class RecoveryBoundaryRegressions(unittest.TestCase):
    def test_strict_boolean_fields_reject_truthy_values(self):
        for field in (
            "actor_trusted", "owner_approved", "approval_fresh",
            "serialized_gates_released", "dashboard_unhealthy", "evidence_complete",
        ):
            for value in (1, "true", [], {}):
                with self.subTest(field=field, value=value):
                    result = evaluate(dataclasses.replace(valid_request(), **{field: value}))
                    self.assertFalse(result.allowed_simulation)
                    self.assertFalse(result.live_recovery_authorized)

    def test_inflight_requires_literal_false(self):
        for value in (0, None, "", "false", []):
            with self.subTest(value=value):
                result = evaluate(dataclasses.replace(valid_request(), recovery_in_flight=value))
                self.assertEqual(result.reason, "recovery_conflict")
                self.assertFalse(result.allowed_simulation)

    def test_sha_rejects_uppercase_whitespace_and_non_string(self):
        for value in (SHA.upper(), SHA + "\n", " " + SHA, None, b"a" * 40, 123):
            with self.subTest(value=repr(value)):
                result = evaluate(dataclasses.replace(valid_request(), target_sha=value))
                self.assertEqual(result.reason, "invalid_sha")
                self.assertEqual(result.target_sha, "invalid")
                self.assertFalse(result.live_recovery_authorized)

    def test_correlation_rejects_controls_unicode_and_oversize(self):
        for value in ("", "a" * 65, "a\n", "é", "../escape", None, 55):
            with self.subTest(value=repr(value)):
                result = evaluate(dataclasses.replace(valid_request(), request_id=value))
                self.assertEqual(result.reason, "invalid_correlation")
                self.assertEqual(result.correlation_id, "invalid")
                self.assertFalse(result.allowed_simulation)

    def test_sensitive_invalid_inputs_never_echo_in_decision(self):
        secret = "secret:with/slashes"
        result = evaluate(dataclasses.replace(
            valid_request(), target_sha=secret, request_id=secret
        ))
        self.assertNotIn(secret, repr(result))
        self.assertFalse(result.live_recovery_authorized)


if __name__ == "__main__":
    unittest.main()
