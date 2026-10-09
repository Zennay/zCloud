"""Offline, non-authorizing emergency-stop decision reference for zCloud.

No server, worker, network, database or workflow integration. Runtime operator
identity, atomic fencing and side-effect verification remain separate gates.
"""
from dataclasses import dataclass
import re
import unittest

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class StopDecision:
    allowed_offline_only: bool
    reason: str


def evaluate_stop(evidence):
    """Deny unless a fresh operator-approved stop has exclusive fence evidence."""
    if not isinstance(evidence, dict):
        return StopDecision(False, "invalid_evidence")
    required = ("worker_id", "request_id", "generation", "observed_generation",
                "operator_authenticated", "operator_approved", "fence_held",
                "stop_already_pending", "observation_age_seconds")
    if any(key not in evidence for key in required):
        return StopDecision(False, "missing_field")
    if set(evidence) != set(required):
        return StopDecision(False, "unexpected_field")
    for key in ("worker_id", "request_id"):
        value = evidence[key]
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            return StopDecision(False, "invalid_identifier")
    for key in ("generation", "observed_generation"):
        if type(evidence[key]) is not int or not 0 <= evidence[key] <= 2**53 - 1:
            return StopDecision(False, "invalid_generation")
    if evidence["generation"] != evidence["observed_generation"]:
        return StopDecision(False, "generation_changed")
    for key in ("operator_authenticated", "operator_approved", "fence_held",
                "stop_already_pending"):
        if type(evidence[key]) is not bool:
            return StopDecision(False, "invalid_flag")
    if not evidence["operator_authenticated"] or not evidence["operator_approved"]:
        return StopDecision(False, "operator_not_authorized")
    if not evidence["fence_held"]:
        return StopDecision(False, "missing_fence")
    if evidence["stop_already_pending"]:
        return StopDecision(False, "duplicate_pending")
    age = evidence["observation_age_seconds"]
    if type(age) not in (int, float) or not 0 <= age <= 30:
        return StopDecision(False, "stale_observation")
    return StopDecision(True, "admitted_offline_only")


class EmergencyStopOfflineTests(unittest.TestCase):
    def setUp(self):
        self.good = dict(worker_id="worker-1", request_id="stop:20261009:1",
                         generation=8, observed_generation=8,
                         operator_authenticated=True, operator_approved=True,
                         fence_held=True, stop_already_pending=False,
                         observation_age_seconds=0)

    def check_denied(self, changes):
        evidence = dict(self.good, **changes)
        self.assertFalse(evaluate_stop(evidence).allowed_offline_only)

    def test_explicit_positive_is_non_authorizing(self):
        self.assertEqual(evaluate_stop(self.good),
                         StopDecision(True, "admitted_offline_only"))

    def test_missing_and_invalid_evidence(self):
        self.assertFalse(evaluate_stop(None).allowed_offline_only)
        for key in self.good:
            evidence = dict(self.good)
            evidence.pop(key)
            self.assertFalse(evaluate_stop(evidence).allowed_offline_only)

    def test_unexpected_fields_fail_closed(self):
        self.check_denied({"override_fence": True})
        self.check_denied({"operator_role": "admin"})
        self.assertEqual(evaluate_stop(dict(self.good, override_fence=True)).reason,
                         "unexpected_field")

    def test_ids_reject_injection_and_oversize(self):
        for value in ("", "../escape", "id with space", "x"*129, "é", "\n"):
            self.check_denied({"worker_id": value})
            self.check_denied({"request_id": value})

    def test_generation_fencing(self):
        for value in (True, -1, 2**53, 8.0):
            self.check_denied({"generation": value})
        self.check_denied({"observed_generation": 9})

    def test_flags_are_strict(self):
        for key in ("operator_authenticated", "operator_approved", "fence_held",
                    "stop_already_pending"):
            self.check_denied({key: 1})
        for key in ("operator_authenticated", "operator_approved", "fence_held"):
            self.check_denied({key: False})
        self.check_denied({"stop_already_pending": True})

    def test_age_boundaries(self):
        for value in (-1, 30.01, float("nan"), float("inf"), "0", True):
            self.check_denied({"observation_age_seconds": value})
        self.assertTrue(evaluate_stop(dict(self.good, observation_age_seconds=30)).allowed_offline_only)


if __name__ == "__main__":
    unittest.main()
