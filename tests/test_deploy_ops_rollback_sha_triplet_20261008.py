"""Offline rollback SHA-triplet consistency tests; never authorizes deployment.

Only validates the shape of claimed evidence. It does not authenticate evidence,
query GitHub or VPS, or replace serialized #580 / #1089 release admission.
"""
import re
import unittest

_SHA = re.compile(r"[0-9a-f]{40}\Z")

def classify_triplet(candidate, previous, deployed, *, verified_rollback=False):
    """Return a deny-by-default evidence classification, never an approval."""
    if type(verified_rollback) is not bool:
        return {"status": "INVALID_ROLLBACK_CLAIM", "authorized": False}
    entries = (candidate, previous, deployed)
    if not all(isinstance(v, str) and _SHA.fullmatch(v) for v in entries):
        return {"status": "INVALID_SHA", "authorized": False}
    if candidate == previous:
        return {"status": "NO_DISTINCT_ROLLBACK_TARGET", "authorized": False}
    if deployed not in (candidate, previous):
        return {"status": "UNRECOGNIZED_DEPLOYED_SHA", "authorized": False}
    if deployed == previous and verified_rollback is not True:
        return {"status": "ROLLBACK_UNVERIFIED", "authorized": False}
    if deployed == candidate and verified_rollback is True:
        return {"status": "CONTRADICTORY_ROLLBACK_CLAIM", "authorized": False}
    return {"status": "SHAPE_ONLY_UNVERIFIED", "authorized": False}

class RollbackTripletTests(unittest.TestCase):
    C = "a" * 40
    P = "b" * 40
    X = "c" * 40

    def check(self, expected, candidate="DEFAULT", previous="DEFAULT", deployed="DEFAULT", **kwargs):
        result = classify_triplet(self.C if candidate == "DEFAULT" else candidate,
                                  self.P if previous == "DEFAULT" else previous,
                                  self.C if deployed == "DEFAULT" else deployed, **kwargs)
        self.assertEqual(expected, result["status"])
        self.assertIs(result["authorized"], False)

    def test_claimed_deploy_is_never_authorization(self):
        self.check("SHAPE_ONLY_UNVERIFIED")

    def test_claimed_rollback_is_never_authorization(self):
        self.check("SHAPE_ONLY_UNVERIFIED", deployed=self.P, verified_rollback=True)

    def test_rollback_requires_independent_verification(self):
        self.check("ROLLBACK_UNVERIFIED", deployed=self.P)

    def test_contradictory_rollback_claim(self):
        self.check("CONTRADICTORY_ROLLBACK_CLAIM", verified_rollback=True)

    def test_unrecognized_target(self):
        self.check("UNRECOGNIZED_DEPLOYED_SHA", deployed=self.X)

    def test_candidate_must_differ_from_previous(self):
        self.check("NO_DISTINCT_ROLLBACK_TARGET", previous=self.C)

    def test_rollback_claim_must_be_boolean(self):
        for invalid in (None, 0, 1, "true", [], {}):
            with self.subTest(value=invalid):
                self.check("INVALID_ROLLBACK_CLAIM", verified_rollback=invalid)

    def test_sha_must_be_immutable_and_canonical(self):
        for invalid in ("main", "A" * 40, "a" * 39, "a" * 41, "", "a" * 40 + "\n", None, 4):
            with self.subTest(value=invalid):
                self.check("INVALID_SHA", deployed=invalid)
                self.check("INVALID_SHA", candidate=invalid if invalid is not None else "")
                self.check("INVALID_SHA", previous=invalid if invalid is not None else "")

    def test_all_canonical_deployed_destinations_deny_authorization(self):
        for deployed, claim, status in (
            (self.C, False, "SHAPE_ONLY_UNVERIFIED"),
            (self.C, True, "CONTRADICTORY_ROLLBACK_CLAIM"),
            (self.P, False, "ROLLBACK_UNVERIFIED"),
            (self.P, True, "SHAPE_ONLY_UNVERIFIED"),
            (self.X, False, "UNRECOGNIZED_DEPLOYED_SHA"),
            (self.X, True, "UNRECOGNIZED_DEPLOYED_SHA"),
        ):
            with self.subTest(deployed=deployed, verified_rollback=claim):
                self.check(status, deployed=deployed, verified_rollback=claim)

    def test_sha_type_confusion_is_denied_in_every_position(self):
        invalid_values = (b"a" * 40, bytearray(b"a" * 40), 0, True, False,
                          ["a" * 40], {"sha": "a" * 40}, ("a" * 40,))
        for invalid in invalid_values:
            with self.subTest(type=type(invalid).__name__):
                self.check("INVALID_SHA", candidate=invalid)
                self.check("INVALID_SHA", previous=invalid)
                self.check("INVALID_SHA", deployed=invalid)

    def test_noncanonical_unicode_and_whitespace_are_rejected(self):
        invalid_values = (" a" * 40, "a" * 40 + " ", "\\u0430" * 40,
                          "a" * 40 + "\\r", "a" * 40 + "\\x00")
        for invalid in invalid_values:
            with self.subTest(value=repr(invalid)):
                self.check("INVALID_SHA", deployed=invalid)

    def test_identical_candidate_and_previous_never_indicate_rollback(self):
        for deployed in (self.C, self.P, self.X):
            for claim in (False, True):
                with self.subTest(deployed=deployed, claim=claim):
                    self.check("NO_DISTINCT_ROLLBACK_TARGET", previous=self.C,
                               deployed=deployed, verified_rollback=claim)

if __name__ == "__main__":
    unittest.main()
