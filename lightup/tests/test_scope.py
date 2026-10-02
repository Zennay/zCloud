import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lightup.models import Authorization, Target
from lightup.scope import ScopePolicy, ScopeReason


class ScopePolicyTests(unittest.TestCase):
    def test_loopback_allowed(self):
        decision = ScopePolicy().decide(Target("127.0.0.1"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.LOOPBACK)

    def test_private_lab_allowed_by_default(self):
        decision = ScopePolicy().decide(Target("10.20.30.40"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.PRIVATE_LAB)

    def test_unknown_public_ip_fails_closed(self):
        decision = ScopePolicy().decide(Target("8.8.8.8"))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.OUT_OF_SCOPE)

    def test_public_network_needs_authorization(self):
        policy = ScopePolicy(explicit_networks=("8.8.8.0/24",))
        decision = policy.decide(Target("8.8.8.8"))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.AUTHORIZATION_MISSING)

    def test_explicit_public_host_with_current_authorization_allowed(self):
        auth = Authorization(owner="example-owner", reference="AUTH-001")
        policy = ScopePolicy(explicit_hosts=frozenset({"security.example.test"}))
        decision = policy.decide(Target("https://security.example.test/path", authorization=auth))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.EXPLICIT_HOST)

    def test_expired_authorization_denied(self):
        auth = Authorization(
            owner="example-owner",
            reference="AUTH-OLD",
            valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        policy = ScopePolicy(explicit_hosts=frozenset({"security.example.test"}))
        decision = policy.decide(Target("security.example.test", authorization=auth))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, ScopeReason.AUTHORIZATION_EXPIRED)


if __name__ == "__main__":
    unittest.main()
