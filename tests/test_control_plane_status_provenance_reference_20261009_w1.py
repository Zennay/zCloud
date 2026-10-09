"""Offline reference-only provenance tests for PR #1253.

These tests do not import zCloud runtime modules, access production, or grant
privileged actions. They specify expected display-only behavior for future
consumer integration; green tests DO NOT verify a deployed implementation.
"""
import math
import unittest


def display_evidence(source_sha, ci, production, worker, claim, approval, *, now=2000):
    """Fixture-local reference decision, intentionally not a production policy."""
    result = {"ci": "unknown", "production": "unverified",
              "worker": "activity_unknown", "ownership": "unknown",
              "admission": "denied", "privileged_actions": []}
    if isinstance(ci, dict) and ci.get("sha") == source_sha and all(
        isinstance(ci.get(key), int) and not isinstance(ci.get(key), bool)
        and ci[key] > 0 for key in ("run_id", "attempt")
    ) and ci.get("conclusion") == "success":
        result["ci"] = "verified"
    if isinstance(production, dict):
        if production.get("sha") != source_sha and production.get("sha"):
            result["production"] = "mismatch"
        elif (production.get("sha") == source_sha
              and production.get("environment") == "production"
              and result["ci"] == "verified"
              and production.get("run_id") == ci["run_id"]
              and production.get("attempt") == ci["attempt"]
              and production.get("status") == "healthy"
              and isinstance(production.get("observed_at"), (int, float))
              and not isinstance(production.get("observed_at"), bool)
              and math.isfinite(production["observed_at"])
              and 0 <= now - production["observed_at"] <= 60):
            result["production"] = "observed_healthy"
    if isinstance(worker, dict):
        observed = worker.get("last_action_at")
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            result["worker"] = ("active_observed" if 0 <= now - observed <= 60
                                else "stale")
        elif worker.get("configured"):
            result["worker"] = "configured_only"
    if isinstance(claim, dict) and claim.get("available") is True:
        result["ownership"] = "requires_atomic_acquire"
    # Even an apparent approval is never authorization in a display projector.
    return result


class OfflineProvenanceMatrix(unittest.TestCase):
    def setUp(self):
        self.ci = {"sha": "A", "run_id": 7, "attempt": 2,
                   "conclusion": "success"}
        self.prod = {"sha": "A", "run_id": 7, "attempt": 2,
                     "environment": "production", "status": "healthy",
                     "observed_at": 1990}

    def check(self, expected, **changes):
        inputs = dict(source_sha="A", ci=self.ci, production=self.prod,
                      worker=None, claim=None, approval=None)
        inputs.update(changes)
        result = display_evidence(**inputs)
        self.assertEqual(result["privileged_actions"], [])
        self.assertEqual(result[expected[0]], expected[1])
        self.assertEqual(result["admission"], "denied")
        return result

    def test_missing_production_status(self):
        result = self.check(("ci", "verified"), production=None)
        self.assertEqual(result["production"], "unverified")

    def test_main_drift(self):
        self.check(("production", "mismatch"), source_sha="B")

    def test_attempt_mismatch(self):
        self.check(("production", "unverified"),
                   production={**self.prod, "attempt": 1})

    def test_configured_worker_without_action(self):
        self.check(("worker", "configured_only"),
                   worker={"configured": True})

    def test_stale_worker_action(self):
        self.check(("worker", "stale"),
                   worker={"configured": True, "last_action_at": 100})

    def test_claim_api_failure(self):
        self.check(("ownership", "unknown"), claim=None)

    def test_project_scoped_claims_are_not_ownership(self):
        for project in ("alpha", "beta"):
            result = self.check(("ownership", "requires_atomic_acquire"),
                                claim={"project_id": project, "claim_key": "same",
                                       "available": True})
            self.assertNotEqual(result["ownership"], "owned")

    def test_missing_approval(self):
        self.check(("admission", "denied"), approval=None)

    def test_malformed_evidence(self):
        for value in (None, {}, [], "null", {"status": "healthy"}):
            with self.subTest(value=value):
                self.check(("ci", "unknown"), ci=value, production=value)

    def test_run_id_mismatch(self):
        self.check(("production", "unverified"),
                   production={**self.prod, "run_id": 9})

    def test_approval_does_not_grant_display_authority(self):
        self.check(("admission", "denied"), approval={"approved": True})

    def test_production_timestamp_not_current(self):
        for timestamp in (None, True, float("nan"), float("inf"), -1, 1939, 2001):
            with self.subTest(timestamp=timestamp):
                self.check(("production", "unverified"),
                           production={**self.prod, "observed_at": timestamp})

    def test_fresh_matching_production_observation(self):
        self.check(("production", "observed_healthy"))

    def test_wrong_environment_denies_healthy(self):
        for environment in ("staging", "", None, "Production"):
            with self.subTest(environment=environment):
                self.check(("production", "unverified"),
                           production={**self.prod, "environment": environment})

    def test_unsuccessful_ci_denies_healthy(self):
        for conclusion in ("failure", "cancelled", "neutral", None):
            with self.subTest(conclusion=conclusion):
                result = self.check(("ci", "unknown"),
                                    ci={**self.ci, "conclusion": conclusion})
                self.assertEqual(result["production"], "unverified")

    def test_unhealthy_production_status_denies_healthy(self):
        for status in ("failed", "pending", "unknown", None):
            with self.subTest(status=status):
                self.check(("production", "unverified"),
                           production={**self.prod, "status": status})

    def test_freshness_boundaries(self):
        for timestamp in (1940, 2000):
            with self.subTest(timestamp=timestamp):
                self.check(("production", "observed_healthy"),
                           production={**self.prod, "observed_at": timestamp})

    def test_boolean_attempt_not_integer(self):
        self.check(("ci", "unknown"), ci={**self.ci, "attempt": True})


if __name__ == "__main__":
    unittest.main()
