"""Deny-by-default authorization matrix for issue #1177 (offline only)."""
import dataclasses
import unittest

from scripts.zcloud_dashboard_recovery_offline_gate import RecoveryRequest, evaluate

SHA = "a" * 40


def approved():
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
        request_id="receipt_1177",
    )


class DenyMatrix(unittest.TestCase):
    def test_only_simulated_plan_passes(self):
        result = evaluate(approved())
        self.assertTrue(result.allowed_simulation)
        self.assertFalse(result.live_recovery_authorized)
        self.assertEqual(result.reason, "simulated_plan_only")
        self.assertEqual(result.target_sha, SHA)
        self.assertEqual(result.correlation_id, "receipt_1177")

    def test_denials(self):
        cases = {
            "pull_request": ("event", "pull_request", "untrusted_event"),
            "push": ("event", "push", "untrusted_event"),
            "repo": ("repository", "attacker/repo", "untrusted_repository"),
            "actor": ("actor_trusted", False, "untrusted_actor"),
            "ref": ("ref", "refs/heads/evil", "untrusted_ref"),
            "invalid_sha": ("target_sha", "not-a-sha", "invalid_sha"),
            "missing_sha": ("canonical_sha", "", "invalid_sha"),
            "stale_sha": ("canonical_sha", "b" * 40, "stale_sha"),
            "approval": ("owner_approved", False, "approval_missing"),
            "expired": ("approval_fresh", False, "approval_expired"),
            "gates": ("serialized_gates_released", False, "serialized_gate_closed"),
            "healthy": ("dashboard_unhealthy", False, "dashboard_healthy_or_unknown"),
            "evidence": ("evidence_complete", False, "evidence_incomplete"),
            "collision": ("recovery_in_flight", True, "recovery_conflict"),
            "receipt": ("request_id", "token:very-secret", "invalid_correlation"),
        }
        for name, (field, value, reason) in cases.items():
            with self.subTest(name=name):
                result = evaluate(dataclasses.replace(approved(), **{field: value}))
                self.assertFalse(result.allowed_simulation)
                self.assertFalse(result.live_recovery_authorized)
                self.assertEqual(result.reason, reason)
                self.assertNotIn("very-secret", repr(result))

    def test_unknown_is_denied(self):
        for field in ("actor_trusted", "owner_approved", "approval_fresh",
                      "serialized_gates_released", "dashboard_unhealthy",
                      "evidence_complete", "recovery_in_flight"):
            with self.subTest(field=field):
                self.assertFalse(evaluate(dataclasses.replace(approved(), **{field: None})).allowed_simulation)


if __name__ == "__main__":
    unittest.main()
