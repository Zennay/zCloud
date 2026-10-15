"""Offline fail-closed reference contract for deploy production-status SHA binding.

This file does not change deploy behavior or grant release authority.
Run: python3 -m unittest discover -s tests -p 'test_deploy_status_sha_denial_fixtures_20261008.py'
"""
import re
import unittest

SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_CONTEXT = "zcloud/vps-production"


def assess_status(candidate_sha, response, final_main_sha):
    """Pure admission evidence validation. Never accesses network or VPS."""
    if not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha):
        return "deny:invalid_candidate_sha"
    if not isinstance(final_main_sha, str) or not SHA.fullmatch(final_main_sha):
        return "deny:invalid_final_main_sha"
    if candidate_sha != final_main_sha:
        return "deny:main_drift"
    if not isinstance(response, dict):
        return "deny:missing_response"
    observed = response.get("sha")
    if not isinstance(observed, str) or not SHA.fullmatch(observed):
        return "deny:invalid_response_sha"
    if observed != candidate_sha:
        return "deny:response_sha_mismatch"
    statuses = response.get("statuses")
    if not isinstance(statuses, list):
        return "deny:malformed_statuses"
    matched = [s for s in statuses if isinstance(s, dict) and s.get("context") == EXPECTED_CONTEXT]
    if not matched:
        return "deny:missing_production_context"
    if any(s.get("state") not in ("success", "failure", "pending", "error") for s in matched):
        return "deny:malformed_production_state"
    # The newest matching record must be selected by upstream trustworthy ordering;
    # ambiguous duplicate contexts are denied instead of guessing recency.
    if len(matched) != 1:
        return "deny:ambiguous_production_context"
    return "already_green" if matched[0]["state"] == "success" else "not_green"


class StatusSHAContractTests(unittest.TestCase):
    A = "a" * 40
    B = "b" * 40

    def payload(self, sha=None, state="success"):
        return {"sha": self.A if sha is None else sha,
                "statuses": [{"context": EXPECTED_CONTEXT, "state": state}]}

    def test_stable_exact_sha(self):
        self.assertEqual(assess_status(self.A, self.payload(), self.A), "already_green")

    def test_non_green_exact_sha(self):
        self.assertEqual(assess_status(self.A, self.payload(state="pending"), self.A), "not_green")

    def test_moving_main_denied(self):
        self.assertEqual(assess_status(self.A, self.payload(), self.B), "deny:main_drift")

    def test_stale_success_denied(self):
        self.assertEqual(assess_status(self.A, self.payload(self.B), self.A), "deny:response_sha_mismatch")

    def test_missing_or_invalid_response_sha_denied(self):
        for bad in ("", "not-a-sha", "ABC", None):
            with self.subTest(bad=bad):
                self.assertEqual(assess_status(self.A, self.payload(bad), self.A),
                                 "deny:invalid_response_sha")

    def test_malformed_response_denied(self):
        for bad in (None, {}, [], {"sha": self.A, "statuses": None}):
            with self.subTest(bad=bad):
                self.assertTrue(assess_status(self.A, bad, self.A).startswith("deny:"))

    def test_duplicate_context_denied(self):
        p = self.payload()
        p["statuses"].append(dict(p["statuses"][0]))
        self.assertEqual(assess_status(self.A, p, self.A), "deny:ambiguous_production_context")

    def test_missing_context_denied(self):
        p = self.payload()
        p["statuses"] = []
        self.assertEqual(assess_status(self.A, p, self.A), "deny:missing_production_context")

    def test_invalid_candidate_or_final_head_denied(self):
        self.assertEqual(assess_status("main", self.payload(), self.A), "deny:invalid_candidate_sha")
        self.assertEqual(assess_status(self.A, self.payload(), "main"), "deny:invalid_final_main_sha")


if __name__ == "__main__":
    unittest.main()
