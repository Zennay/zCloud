"""Non-authorizing deploy witness linter regressions."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "witness_lint", ROOT / "scripts" / "zcloud_deploy_gate_witness_lint.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sample():
    s = {name: "evidence-link-or-unknown" for name in MODULE.REQUIRED}
    for name in ("main_sha", "candidate_sha", "candidate_base_sha"):
        s[name] = "a" * 40
    s["witness_type"] = "read_only_non_authorizing"
    s["changed_paths"] = []
    s["exact_head_checks"] = []
    s["main_unchanged_on_recheck"] = "unknown"
    for name in MODULE.DENIAL:
        s[name] = False
    return s


class WitnessLintTests(unittest.TestCase):
    def test_complete_non_authorizing_envelope(self):
        self.assertEqual(MODULE.validate_witness(sample()), [])

    def test_absence_fails_closed(self):
        value = sample()
        del value["gate_1089"]
        self.assertIn("missing:gate_1089", MODULE.validate_witness(value))

    def test_authorization_flags_never_true_or_missing(self):
        for field in MODULE.DENIAL:
            for bad in (True, None, "false", 0):
                with self.subTest(field=field, bad=bad):
                    value = sample()
                    value[field] = bad
                    self.assertIn("must_be_false:" + field, MODULE.validate_witness(value))

    def test_sha_must_be_full_lower_hex(self):
        for bad in ("abc123", "A" * 40, "g" * 40, None, 123):
            with self.subTest(bad=bad):
                value = sample()
                value["candidate_sha"] = bad
                self.assertIn("invalid:candidate_sha", MODULE.validate_witness(value))

    def test_lists_are_required(self):
        value = sample()
        value["exact_head_checks"] = "success"
        self.assertIn("invalid:exact_head_checks", MODULE.validate_witness(value))

    def test_invalid_recheck_disallowed(self):
        value = sample()
        value["main_unchanged_on_recheck"] = "yes"
        self.assertIn("invalid:main_unchanged_on_recheck", MODULE.validate_witness(value))


if __name__ == "__main__":
    unittest.main()
