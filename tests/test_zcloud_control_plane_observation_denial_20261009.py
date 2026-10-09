"""Adversarial, stdlib-only offline denial regression tests."""
import unittest
from scripts.zcloud_control_plane_observation_denial_20261009 import (
    DISALLOWED_ACTIONS, classify_observation,
)

class ObservationDenialTests(unittest.TestCase):
    def test_each_privileged_action_denied_even_with_apparent_proof(self):
        for action in sorted(DISALLOWED_ACTIONS):
            with self.subTest(action=action):
                result = classify_observation({
                    "action": action, "source": "trusted-vps",
                    "status": "success", "verified": True,
                    "approval": True, "run_id": 123, "fresh": True,
                })
                self.assertEqual(result, {
                    "authorized": False, "mutation_performed": False,
                    "reason": "observation_not_authority",
                })

    def test_malformed_inputs_fail_closed(self):
        for payload in (None, [], "restart_worker", {}, {"action": None},
                        {"action": False}, {"action": 1}, {"action": "evil"}):
            with self.subTest(payload=repr(payload)):
                result = classify_observation(payload)
                self.assertIs(result["authorized"], False)
                self.assertIs(result["mutation_performed"], False)

    def test_output_never_echoes_sensitive_input(self):
        secret = "private-observation-secret-canary"
        result = classify_observation({"action": "deploy", "token": secret,
                                       "reason": secret, "headers": {"Authorization": secret}})
        self.assertNotIn(secret, repr(result))
        self.assertEqual(set(result), {"authorized", "mutation_performed", "reason"})

    def test_action_must_match_exact_identifier(self):
        for action in ("Deploy", "deploy ", " deploy", "restart-worker", ""):
            self.assertEqual(classify_observation({"action": action})["reason"],
                             "unknown_action")

if __name__ == "__main__":
    unittest.main()
