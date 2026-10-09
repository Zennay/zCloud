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

    def test_spoofed_authorization_keys_cannot_override_denial(self):
        for action in sorted(DISALLOWED_ACTIONS):
            with self.subTest(action=action):
                result = classify_observation({
                    "action": action,
                    "authorized": True,
                    "mutation_performed": True,
                    "reason": "approved",
                    "source": "signed",
                    "producer_authenticated": True,
                    "approval": {"admin": True},
                })
                self.assertFalse(result["authorized"])
                self.assertFalse(result["mutation_performed"])
                self.assertEqual(result["reason"], "observation_not_authority")

    def test_result_is_not_shared_between_calls(self):
        first = classify_observation({"action": "deploy"})
        first["authorized"] = True
        first["reason"] = "forged"
        second = classify_observation({"action": "deploy"})
        self.assertIs(second["authorized"], False)
        self.assertEqual(second["reason"], "observation_not_authority")

    def test_non_string_keys_and_unusual_mapping_values(self):
        for payload in ({1: "deploy"}, {None: "deploy"}, {"action": []},
                        {"action": {}}, {"action": ("deploy",)}):
            with self.subTest(payload=repr(payload)):
                result = classify_observation(payload)
                self.assertFalse(result["authorized"])
                self.assertFalse(result["mutation_performed"])

if __name__ == "__main__":
    unittest.main()
