import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lightup.activation import ActivationGate, ActivationMode, ActivationPolicy
from lightup.models import Authorization, Target
from lightup.scope import ScopePolicy


class ActivationTests(unittest.TestCase):
    def test_plan_only_never_issues_execution_permit(self):
        gate = ActivationGate(ScopePolicy(), ActivationPolicy())
        with self.assertRaises(PermissionError):
            gate.issue(Target("127.0.0.1"), "web-baseline")

    def test_lab_only_can_issue_private_permit(self):
        gate = ActivationGate(
            ScopePolicy(),
            ActivationPolicy(mode=ActivationMode.LAB_ONLY, activation_reference="LAB-TEST"),
        )
        permit = gate.issue(Target("10.10.0.5"), "web-baseline")
        self.assertEqual(permit.mode, ActivationMode.LAB_ONLY)

    def test_lab_only_rejects_explicit_public_target(self):
        policy = ScopePolicy(explicit_hosts=frozenset({"authorized.example.test"}))
        gate = ActivationGate(
            policy,
            ActivationPolicy(mode=ActivationMode.LAB_ONLY, activation_reference="LAB-TEST"),
        )
        target = Target(
            "authorized.example.test",
            authorization=Authorization(owner="owner", reference="AUTH-1"),
        )
        with self.assertRaises(PermissionError):
            gate.issue(target, "web-baseline")


if __name__ == "__main__":
    unittest.main()
