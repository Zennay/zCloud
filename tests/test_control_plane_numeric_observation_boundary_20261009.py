"""Offline-only, deny-first resource observation contract.

This deliberately does not feed the live scheduler or grant control authority.
"""
import math
import unittest


def classify_resource_observation(payload):
    """Accept only fully bounded exact JSON-style numeric evidence."""
    if type(payload) is not dict:
        return {"valid": False, "reason": "invalid_envelope", "authorizes_action": False}
    if set(payload) != {"cpu_percent", "memory_percent", "disk_percent", "sample_age_seconds"}:
        return {"valid": False, "reason": "invalid_fields", "authorizes_action": False}
    for key in ("cpu_percent", "memory_percent", "disk_percent", "sample_age_seconds"):
        value = payload[key]
        if type(value) not in (float, int) or not math.isfinite(value):
            return {"valid": False, "reason": "invalid_number", "authorizes_action": False}
        if value < 0 or value > (300 if key == "sample_age_seconds" else 100):
            return {"valid": False, "reason": "out_of_range", "authorizes_action": False}
    return {"valid": True, "reason": "observation_only", "authorizes_action": False}


class ResourceObservationBoundaryTests(unittest.TestCase):
    good = {"cpu_percent": 28, "memory_percent": 42.5, "disk_percent": 36, "sample_age_seconds": 10}

    def test_good_does_not_authorize(self):
        self.assertEqual(classify_resource_observation(self.good), {
            "valid": True, "reason": "observation_only", "authorizes_action": False})

    def test_missing_and_extra_fields(self):
        for bad in ({**self.good, "restart": True},
                    {k: v for k, v in self.good.items() if k != "disk_percent"}):
            self.assertFalse(classify_resource_observation(bad)["valid"])

    def test_spoofed_mapping_and_dict_subclass(self):
        class Spoof(dict):
            pass
        for bad in (Spoof(self.good), [self.good], None, "healthy"):
            self.assertFalse(classify_resource_observation(bad)["valid"])

    def test_nonfinite_and_boolean(self):
        for bad_value in (float("nan"), float("inf"), -float("inf"), True, False, None, "50"):
            for key in self.good:
                self.assertFalse(classify_resource_observation({**self.good, key: bad_value})["valid"])

    def test_out_of_range(self):
        for key in self.good:
            upper = 300 if key == "sample_age_seconds" else 100
            for number in (-1, upper + 0.001):
                self.assertFalse(classify_resource_observation({**self.good, key: number})["valid"])

    def test_boundaries_are_observation_only(self):
        for value in (0, 100):
            reading = {**self.good, "cpu_percent": value, "memory_percent": value, "disk_percent": value}
            self.assertFalse(classify_resource_observation(reading)["authorizes_action"])
        self.assertTrue(classify_resource_observation({**self.good, "sample_age_seconds": 300})["valid"])


if __name__ == "__main__":
    unittest.main()
