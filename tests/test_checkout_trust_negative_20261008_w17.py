"""Offline fail-closed checkout trust fixtures for issue #633.

This is a reference contract only. It does not authorize or modify workflow jobs.
Run: python3 -m unittest discover -s tests -p 'test_checkout_trust_negative_20261008_w17.py'
"""
import re
import unittest

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
CHECKOUT = re.compile(r"\Aactions/checkout@([0-9a-f]{40})\Z")


def evaluate_checkout(step, *, expected_revision):
    """Return a bounded reason; empty string means only local syntactic checks passed."""
    if not isinstance(step, dict) or not isinstance(expected_revision, str):
        return "invalid_input"
    if not SHA40.fullmatch(expected_revision):
        return "invalid_expected_revision"
    uses = step.get("uses")
    if not isinstance(uses, str) or not CHECKOUT.fullmatch(uses):
        return "checkout_not_immutable"
    settings = step.get("with")
    if not isinstance(settings, dict):
        return "checkout_settings_missing"
    if settings.get("persist-credentials") is not False:
        return "credentials_not_disabled"
    if settings.get("ref") != expected_revision:
        return "revision_not_exact"
    if settings.get("repository") is not None:
        return "cross_repository_checkout_requires_separate_review"
    return ""


class CheckoutTrustNegativeFixtures(unittest.TestCase):
    SHA = "a" * 40
    PIN = "d23441a48e516b6c34aea4fa41551a30e30af803"

    def valid_step(self):
        return {
            "uses": "actions/checkout@" + self.PIN,
            "with": {"persist-credentials": False, "ref": self.SHA},
        }

    def test_exact_pinned_credential_free_checkout(self):
        self.assertEqual(evaluate_checkout(self.valid_step(), expected_revision=self.SHA), "")

    def test_floating_tag_denied(self):
        step = self.valid_step()
        step["uses"] = "actions/checkout@v4"
        self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "checkout_not_immutable")

    def test_uppercase_pin_denied(self):
        step = self.valid_step()
        step["uses"] = "actions/checkout@" + self.PIN.upper()
        self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "checkout_not_immutable")

    def test_missing_or_true_credentials_denied(self):
        for value in (None, True, "false", "False", 0):
            with self.subTest(value=value):
                step = self.valid_step()
                if value is None:
                    del step["with"]["persist-credentials"]
                else:
                    step["with"]["persist-credentials"] = value
                self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "credentials_not_disabled")

    def test_symbolic_or_stale_checkout_ref_denied(self):
        for ref in (None, "main", "${{ github.ref }}", "b" * 40):
            with self.subTest(ref=ref):
                step = self.valid_step()
                step["with"]["ref"] = ref
                self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "revision_not_exact")

    def test_invalid_expected_revision_denied(self):
        for rev in ("main", "A" * 40, "a" * 39, "", None):
            with self.subTest(rev=rev):
                self.assertEqual(evaluate_checkout(self.valid_step(), expected_revision=rev), "invalid_expected_revision")

    def test_missing_settings_denied(self):
        step = self.valid_step()
        del step["with"]
        self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "checkout_settings_missing")

    def test_cross_repository_override_denied(self):
        step = self.valid_step()
        step["with"]["repository"] = "external/other"
        self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "cross_repository_checkout_requires_separate_review")

    def test_malformed_step_denied(self):
        for step in (None, [], "", 5):
            with self.subTest(step=step):
                self.assertEqual(evaluate_checkout(step, expected_revision=self.SHA), "invalid_input")


if __name__ == "__main__":
    unittest.main()
