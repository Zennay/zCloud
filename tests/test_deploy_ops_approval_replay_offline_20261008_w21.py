"""Offline DENY-only regression for deployment approval replay/consumption.

This reference fixture never grants production deploy or recovery authority.
No network, runner, service or persisted state access.
"""
import re
import unittest

SHA = re.compile(r"^[0-9a-f]{40}$")
NONCE = re.compile(r"^[a-zA-Z0-9_-]{24,128}$")


def denial_reasons(candidate, approval, consumed_nonces):
    """Return denial reasons; empty means shape-only consistency, NOT admission."""
    failures = []
    if not isinstance(candidate, dict) or not isinstance(approval, dict):
        return ("invalid_payload",)
    if not isinstance(consumed_nonces, (set, frozenset)):
        return ("invalid_consumption_snapshot",)
    for key in ("repository", "environment", "head_sha", "run_id", "run_attempt"):
        if candidate.get(key) != approval.get(key) or not candidate.get(key):
            failures.append("binding_" + key)
    sha = candidate.get("head_sha")
    if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        failures.append("invalid_sha")
    for key in ("run_id", "run_attempt"):
        v = candidate.get(key)
        if type(v) is not int or v < 1:
            failures.append("invalid_" + key)
    nonce = approval.get("nonce")
    if not isinstance(nonce, str) or NONCE.fullmatch(nonce) is None:
        failures.append("invalid_nonce")
    elif nonce in consumed_nonces:
        failures.append("replayed_nonce")
    if approval.get("decision") != "approve":
        failures.append("not_approved")
    if approval.get("expires_at") is None or approval.get("issued_at") is None:
        failures.append("missing_expiry")
    else:
        issued, expires, now = approval.get("issued_at"), approval.get("expires_at"), candidate.get("now")
        if any(type(x) is not int or x < 0 for x in (issued, expires, now)):
            failures.append("invalid_time")
        elif not (issued <= now < expires and expires - issued <= 3600):
            failures.append("expired_or_unbounded")
    return tuple(failures)


def live_deploy_authorized(*_args, **_kwargs):
    """Production admission is intentionally impossible in this fixture."""
    return False


class ApprovalReplayOfflineTests(unittest.TestCase):
    def setUp(self):
        self.c = dict(repository="Zennay/zCloud", environment="production",
                      head_sha="a" * 40, run_id=123, run_attempt=1, now=150)
        self.a = dict(repository="Zennay/zCloud", environment="production",
                      head_sha="a" * 40, run_id=123, run_attempt=1,
                      nonce="nonce_123456789012345678901234", decision="approve",
                      issued_at=100, expires_at=200)

    def test_consistent_shape_still_never_authorizes(self):
        self.assertEqual(denial_reasons(self.c, self.a, set()), ())
        self.assertFalse(live_deploy_authorized(self.c, self.a))

    def test_consumed_nonce(self):
        self.assertIn("replayed_nonce", denial_reasons(self.c, self.a, {self.a["nonce"]}))

    def test_run_attempt_replay(self):
        self.c["run_attempt"] = 2
        self.assertIn("binding_run_attempt", denial_reasons(self.c, self.a, set()))

    def test_run_id_replay(self):
        self.c["run_id"] = 124
        self.assertIn("binding_run_id", denial_reasons(self.c, self.a, set()))

    def test_sha_drift(self):
        self.c["head_sha"] = "b" * 40
        self.assertIn("binding_head_sha", denial_reasons(self.c, self.a, set()))

    def test_cross_environment(self):
        self.c["environment"] = "staging"
        self.assertIn("binding_environment", denial_reasons(self.c, self.a, set()))

    def test_invalid_nonce(self):
        self.a["nonce"] = "short"
        self.assertIn("invalid_nonce", denial_reasons(self.c, self.a, set()))

    def test_expired_or_unbounded_approval(self):
        self.c["now"] = 200
        self.assertIn("expired_or_unbounded", denial_reasons(self.c, self.a, set()))
        self.a["expires_at"] = 999999
        self.assertIn("expired_or_unbounded", denial_reasons(self.c, self.a, set()))

    def test_bad_time_and_boolean_run_id(self):
        self.c["now"] = "150"
        self.assertIn("invalid_time", denial_reasons(self.c, self.a, set()))
        self.c["run_id"] = True
        self.assertIn("invalid_run_id", denial_reasons(self.c, self.a, set()))

    def test_deny_non_approval(self):
        self.a["decision"] = "pending"
        self.assertIn("not_approved", denial_reasons(self.c, self.a, set()))

    def test_missing_expiry(self):
        del self.a["expires_at"]
        self.assertIn("missing_expiry", denial_reasons(self.c, self.a, set()))

    def test_malformed_consumption_snapshot(self):
        self.assertEqual(denial_reasons(self.c, self.a, []),
                         ("invalid_consumption_snapshot",))


if __name__ == "__main__":
    unittest.main()
