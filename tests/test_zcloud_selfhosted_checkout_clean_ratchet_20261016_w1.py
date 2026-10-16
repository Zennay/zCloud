"""Adversarial tests for persistent runner checkout hygiene (#633)."""
import importlib.util
from pathlib import Path
import unittest

PATH = (Path(__file__).resolve().parents[1] / "scripts" /
        "zcloud_selfhosted_checkout_clean_ratchet_20261016_w1.py")
SPEC = importlib.util.spec_from_file_location("checkout_clean_ratchet", PATH)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def sample(clean="true", runner="[self-hosted, zcloud, vps]",
           extra="", uses="actions/checkout@0123456789abcdef"):
    return """name: Test
on: workflow_dispatch
jobs:
  inspect:
    runs-on: %s
    timeout-minutes: 5
    steps:
      - uses: %s
        with:
          persist-credentials: false
          clean: %s
%s
""" % (runner, uses, clean, extra)


class CheckoutCleanTests(unittest.TestCase):
    def find(self, value):
        return guard.analyze(value, ".github/workflows/demo.yml")

    def test_explicit_true_safe(self):
        self.assertEqual(self.find(sample("true")), [])

    def test_default_clean_safe(self):
        self.assertEqual(self.find(sample().replace("          clean: true\n", "")), [])

    def test_false_unsafe(self):
        self.assertEqual(len(self.find(sample("false"))), 1)

    def test_quoted_false_unsafe(self):
        self.assertEqual(len(self.find(sample("'false'"))), 1)

    def test_dynamic_clean_unsafe(self):
        self.assertEqual(len(self.find(sample("$" + "{{ inputs.clean }}"))), 1)

    def test_unexpected_zero_is_unsafe(self):
        self.assertEqual(len(self.find(sample("0"))), 1)

    def test_missing_runner_is_not_selfhosted(self):
        self.assertEqual(self.find(sample("false", runner="ubuntu-latest")), [])

    def test_multiline_selfhosted_runner(self):
        data = sample("false", runner="\n      - self-hosted\n      - zcloud\n      - vps")
        self.assertEqual(len(self.find(data)), 1)

    def test_noncheckout_step_ignored(self):
        data = sample("false", uses="actions/setup-node@pinned")
        self.assertEqual(self.find(data), [])

    def test_duplicate_clean_denied(self):
        data = sample("true", extra="          clean: true\n")
        self.assertTrue(any("duplicate-clean" in row[-1] for row in self.find(data)))

    def test_merge_alias_denied(self):
        data = sample("true", extra="          <<: *unsafe_checkout\n")
        self.assertTrue(any("merged-with-map" in row[-1] for row in self.find(data)))

    def test_inline_map_denied(self):
        data = sample("true").replace(
            "        with:\n          persist-credentials: false\n          clean: true",
            "        with: {clean: false}")
        self.assertTrue(any("inline-with" in row[-1] for row in self.find(data)))

    def test_unsafe_unrelated_job_not_scored(self):
        data = sample("false", runner="ubuntu-latest") + """  other:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: echo ok
"""
        self.assertEqual(self.find(data), [])

    def test_two_selfhosted_checkouts_are_counted(self):
        data = sample("false") + """      - uses: actions/checkout@pin
        with:
          clean: false
"""
        self.assertEqual(len(self.find(data)), 2)

    def test_bad_encoding_bytes_blocked(self):
        with self.assertRaises(guard.Denied):
            self.find(sample("false") + "\tunsafe: key\n")

    def test_oversized_workflow_denied(self):
        with self.assertRaises(guard.Denied):
            self.find("#" + "X" * (guard.MAX_BYTES + 1))

    def test_no_new_debt_is_monotone(self):
        item = self.find(sample("false"))[0]
        old = guard.collections.Counter([item])
        new = guard.collections.Counter([item])
        self.assertFalse(new - old)
        new.update([item])
        self.assertEqual(sum((new - old).values()), 1)

    def test_non_checkout_dynamic_step_not_authorized(self):
        self.assertEqual(self.find(sample("true", uses="other/tool@v1")), [])


if __name__ == "__main__":
    unittest.main()
