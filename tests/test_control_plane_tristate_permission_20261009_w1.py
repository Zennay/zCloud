"""Offline regression vectors; no runtime, queue, service or deploy writes."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "control_plane_tristate_permission_reference_20261009_w1.py"
spec = importlib.util.spec_from_file_location("permission_reference", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DENIED = dict.fromkeys(module.EXPECTED, False)


class TriStatePermissionBoundaryTests(unittest.TestCase):
    def assert_denies(self, candidate, expected):
        result = module.classify_permission_claim(candidate)
        self.assertEqual(expected, result["classification"])
        self.assertIs(result["authorizes_action"], False)

    def test_explicit_boolean_false_does_not_authorize(self):
        self.assert_denies(DENIED, "explicit_denial")

    def test_truthy_permission_still_untrusted(self):
        for name in module.EXPECTED:
            with self.subTest(name=name):
                self.assert_denies({**DENIED, name: True}, "claimed_permission_untrusted")

    def test_ambiguous_boolean_coercions_rejected(self):
        for value in ("false", "true", "", "0", "1", 0, 1, None, [], {}, 0.0, 1.0):
            for name in module.EXPECTED:
                with self.subTest(value=repr(value), name=name):
                    self.assert_denies({**DENIED, name: value}, "invalid")

    def test_missing_and_extra_fields_rejected(self):
        self.assert_denies({}, "invalid")
        self.assert_denies({**DENIED, "external_authority": True}, "invalid")
        for name in module.EXPECTED:
            partial = dict(DENIED)
            del partial[name]
            self.assert_denies(partial, "invalid")

    def test_mapping_subclasses_and_spoofed_dict_rejected(self):
        class SpoofedDict(dict):
            def __getitem__(self, key):
                return False

        class SpoofedKeys(dict):
            def __iter__(self):
                return iter(module.EXPECTED)

        for value in (SpoofedDict(DENIED), SpoofedKeys(DENIED)):
            self.assert_denies(value, "invalid")

    def test_non_mappings_rejected(self):
        for value in (None, [], (), "false", True, 0):
            with self.subTest(value=repr(value)):
                self.assert_denies(value, "invalid")

    def test_never_mutates_input(self):
        candidate = dict(DENIED)
        original = candidate.copy()
        module.classify_permission_claim(candidate)
        self.assertEqual(original, candidate)


if __name__ == "__main__":
    unittest.main()
