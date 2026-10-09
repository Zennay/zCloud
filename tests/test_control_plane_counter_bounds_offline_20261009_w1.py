"""Negative, offline-only regression cases for bounded counter evidence."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from control_plane_counter_bounds_offline_20261009_w1 import MAX_COUNTER, assess

class CounterEvidenceTests(unittest.TestCase):
    def test_advance(self):
        self.assertEqual(assess(0, 1), {"valid": True, "reason": "advanced"})

    def test_equal_is_only_observational(self):
        self.assertEqual(assess(4, 4)["reason"], "equal")
        self.assertEqual(assess(4, 4, allow_equal=False)["reason"], "no_progress")

    def test_reject_rollback(self):
        self.assertEqual(assess(10, 9)["reason"], "rollback")

    def test_reject_negative(self):
        self.assertEqual(assess(-1, 0)["reason"], "out_of_range")
        self.assertEqual(assess(0, -1)["reason"], "out_of_range")

    def test_64_bit_boundary(self):
        self.assertTrue(assess(MAX_COUNTER - 1, MAX_COUNTER)["valid"])
        self.assertEqual(assess(MAX_COUNTER, MAX_COUNTER + 1)["reason"], "out_of_range")

    def test_reject_bool_even_though_python_bool_subclasses_int(self):
        self.assertEqual(assess(False, 1)["reason"], "non_integer")
        self.assertEqual(assess(1, True)["reason"], "non_integer")

    def test_reject_float_and_string(self):
        for value in (1.0, "1", None, [], {}, float("inf"), float("nan")):
            with self.subTest(value=repr(value)):
                self.assertEqual(assess(0, value)["reason"], "non_integer")

    def test_arbitrary_precision_is_not_implicitly_admitted(self):
        self.assertEqual(assess(0, 10**100)["reason"], "out_of_range")

    def test_invalid_previous_counter_is_rejected(self):
        for value in (None, True, False, "0", 0.0, [], {}, float("nan"), -1, MAX_COUNTER + 1):
            with self.subTest(previous=repr(value)):
                self.assertFalse(assess(value, 1)["valid"])

    def test_max_boundary_equality_and_rollback(self):
        self.assertEqual(assess(MAX_COUNTER, MAX_COUNTER)["reason"], "equal")
        self.assertEqual(assess(MAX_COUNTER, MAX_COUNTER, allow_equal=False)["reason"], "no_progress")
        self.assertEqual(assess(MAX_COUNTER, MAX_COUNTER - 1)["reason"], "rollback")

    def test_no_progress_does_not_override_bad_evidence(self):
        self.assertEqual(assess(-1, -1, allow_equal=False)["reason"], "out_of_range")
        self.assertEqual(assess(True, True, allow_equal=False)["reason"], "non_integer")

if __name__ == "__main__":
    unittest.main()
