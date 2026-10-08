"""Offline, fail-closed dashboard recovery authorization contract (issue #1177).

This is a simulation only. It does not invoke shell, network, GitHub, VPS, or
production workflow code and never grants permission to execute a recovery.
"""
import dataclasses
import re
import unittest

SHA = "a" * 40
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclasses.dataclass(frozen=True)
class Evidence:
    event: str = "workflow_dispatch"
    repository: str = "Zennay/zCloud"
    ref: str = "refs/heads/main"
    actor_trusted: bool = True
    requested_sha: str = SHA
    current_main_sha: str = SHA
    approval_valid: bool = True
    approval_sha: str = SHA
    gate_580_released: bool = True
    gate_1089_released: bool = True
    dashboard_unhealthy: bool = True
    evidence_complete: bool = True
    recovery_inflight: bool = False
    request_id: str = "req-1177"
    receipt_id: str = "receipt-1177"


def authorize_offline(e):
    """Return an offline decision; never a credential or execution capability."""
    checks = (
        ("untrusted_event", e.event == "workflow_dispatch"),
        ("untrusted_repository", e.repository == "Zennay/zCloud"),
        ("untrusted_ref", e.ref == "refs/heads/main"),
        ("untrusted_actor", e.actor_trusted is True),
        ("invalid_sha", isinstance(e.requested_sha, str) and bool(HEX_SHA.fullmatch(e.requested_sha))),
        ("stale_main", e.requested_sha == e.current_main_sha),
        ("missing_approval", e.approval_valid is True and e.approval_sha == e.requested_sha),
        ("serialized_gate_active", e.gate_580_released is True and e.gate_1089_released is True),
        ("healthy_dashboard", e.dashboard_unhealthy is True),
        ("incomplete_evidence", e.evidence_complete is True),
        ("conflicting_recovery", e.recovery_inflight is False),
        ("missing_correlation", bool(e.request_id) and bool(e.receipt_id)),
    )
    reason = next((code for code, passed in checks if not passed), "simulated_plan_only")
    return {"allowed_in_simulation": reason == "simulated_plan_only",
            "reason": reason,
            "target_sha": e.requested_sha,
            "request_id": e.request_id,
            "receipt_id": e.receipt_id}


class RecoveryAuthorizationMatrix(unittest.TestCase):
    def test_simulated_valid_case_still_has_no_execution(self):
        result = authorize_offline(Evidence())
        self.assertTrue(result["allowed_in_simulation"])
        self.assertEqual(result["reason"], "simulated_plan_only")
        self.assertNotIn("command", result)
        self.assertNotIn("token", result)

    def test_negative_matrix(self):
        cases = (
            ("event", "pull_request", "untrusted_event"),
            ("event", "push", "untrusted_event"),
            ("event", "schedule", "untrusted_event"),
            ("repository", "someone/zCloud", "untrusted_repository"),
            ("ref", "refs/heads/feature", "untrusted_ref"),
            ("actor_trusted", False, "untrusted_actor"),
            ("requested_sha", "not-a-sha", "invalid_sha"),
            ("current_main_sha", "b" * 40, "stale_main"),
            ("approval_valid", False, "missing_approval"),
            ("approval_sha", "b" * 40, "missing_approval"),
            ("gate_580_released", False, "serialized_gate_active"),
            ("gate_1089_released", False, "serialized_gate_active"),
            ("dashboard_unhealthy", False, "healthy_dashboard"),
            ("evidence_complete", False, "incomplete_evidence"),
            ("recovery_inflight", True, "conflicting_recovery"),
            ("request_id", "", "missing_correlation"),
            ("receipt_id", "", "missing_correlation"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                decision = authorize_offline(dataclasses.replace(Evidence(), **{field: value}))
                self.assertFalse(decision["allowed_in_simulation"])
                self.assertEqual(decision["reason"], expected)
                self.assertEqual(decision["target_sha"], SHA)
                self.assertEqual(decision["request_id"], "" if field == "request_id" else "req-1177")

    def test_empty_partial_evidence_denied(self):
        self.assertEqual(authorize_offline(dataclasses.replace(Evidence(), approval_valid=None))["reason"],
                         "missing_approval")


if __name__ == "__main__":
    unittest.main()
