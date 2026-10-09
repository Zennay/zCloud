"""Offline, deny-by-default recovery admission contract. No production integration.

Run: python3 -m unittest discover -s tests -p 'test_deploy_recovery_admission_contract_w1.py'
This module must never trigger shell, network, or service operations.
"""
import re
import unittest
from dataclasses import dataclass

SHA = re.compile(r"^[a-f0-9]{40}$")


@dataclass(frozen=True)
class RecoveryEvidence:
    event: str
    repository: str
    ref: str
    actor_trusted: bool
    target_sha: str
    main_sha_before: str
    main_sha_after: str
    approved_sha: str
    approval_fresh: bool
    owner_approved: bool
    serialized_gate_clear: bool
    dashboard_unhealthy: bool
    diagnostic_complete: bool
    no_conflicting_recovery: bool
    request_id: str


def admission(e: RecoveryEvidence):
    """Return (authorized, reason, sanitized receipt); never execute recovery."""
    correlation = e.request_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", e.request_id) else "invalid"
    sha = e.target_sha if SHA.fullmatch(e.target_sha) else "invalid"
    receipt = {"request_id": correlation, "target_sha": sha}
    checks = [
        (e.event == "workflow_dispatch", "event_denied"),
        (e.repository == "Zennay/zCloud", "repository_denied"),
        (e.ref == "refs/heads/main", "ref_denied"),
        (e.actor_trusted, "actor_denied"),
        (bool(SHA.fullmatch(e.target_sha)), "target_sha_invalid"),
        (e.main_sha_before == e.target_sha and e.main_sha_after == e.target_sha, "main_drift"),
        (e.approved_sha == e.target_sha, "approval_sha_mismatch"),
        (e.owner_approved and e.approval_fresh, "approval_missing_or_expired"),
        (e.serialized_gate_clear, "serialized_gate_closed"),
        (e.dashboard_unhealthy, "dashboard_healthy"),
        (e.diagnostic_complete, "diagnostic_incomplete"),
        (e.no_conflicting_recovery, "recovery_conflict"),
    ]
    for ok, reason in checks:
        if not ok:
            return False, reason, receipt
    return True, "offline_simulation_only", receipt


class RecoveryAdmissionContract(unittest.TestCase):
    def setUp(self):
        s = "a" * 40
        self.base = dict(
            event="workflow_dispatch", repository="Zennay/zCloud", ref="refs/heads/main",
            actor_trusted=True, target_sha=s, main_sha_before=s, main_sha_after=s,
            approved_sha=s, approval_fresh=True, owner_approved=True,
            serialized_gate_clear=True, dashboard_unhealthy=True,
            diagnostic_complete=True, no_conflicting_recovery=True, request_id="recovery-001"
        )

    def decide(self, **changes):
        return admission(RecoveryEvidence(**(self.base | changes)))

    def test_offline_simulated_authorized_case(self):
        allowed, reason, receipt = self.decide()
        self.assertTrue(allowed)
        self.assertEqual(reason, "offline_simulation_only")
        self.assertEqual(receipt["request_id"], "recovery-001")

    def test_fail_closed_matrix(self):
        cases = [
            ({"event": "pull_request"}, "event_denied"),
            ({"event": "push"}, "event_denied"),
            ({"repository": "other/repo"}, "repository_denied"),
            ({"ref": "refs/heads/feature"}, "ref_denied"),
            ({"actor_trusted": False}, "actor_denied"),
            ({"target_sha": "main"}, "target_sha_invalid"),
            ({"main_sha_before": "b" * 40}, "main_drift"),
            ({"main_sha_after": "b" * 40}, "main_drift"),
            ({"approved_sha": "b" * 40}, "approval_sha_mismatch"),
            ({"owner_approved": False}, "approval_missing_or_expired"),
            ({"approval_fresh": False}, "approval_missing_or_expired"),
            ({"serialized_gate_clear": False}, "serialized_gate_closed"),
            ({"dashboard_unhealthy": False}, "dashboard_healthy"),
            ({"diagnostic_complete": False}, "diagnostic_incomplete"),
            ({"no_conflicting_recovery": False}, "recovery_conflict"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                allowed, reason, receipt = self.decide(**changes)
                self.assertFalse(allowed)
                self.assertEqual(reason, expected)
                self.assertEqual(receipt["target_sha"], self.base["target_sha"])

    def test_receipt_never_echoes_unsafe_identifiers(self):
        _, _, receipt = self.decide(request_id="secret\nTOKEN=private", actor_trusted=False)
        self.assertEqual(receipt["request_id"], "invalid")
        self.assertNotIn("secret", str(receipt))


if __name__ == "__main__":
    unittest.main()
