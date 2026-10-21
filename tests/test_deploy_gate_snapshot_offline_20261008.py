"""Regression checks for the deploy gate snapshot verifier."""
import datetime as dt
import importlib.util
import pathlib
import unittest

p = pathlib.Path(__file__).resolve().parents[1] / "scripts/deploy_gate_snapshot_offline_20261008.py"
spec = importlib.util.spec_from_file_location("offline_gate", p)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
NOW = dt.datetime(2026, 10, 8, 1, tzinfo=dt.timezone.utc)

def sample():
    return {
        "repository": "Zennay/zCloud",
        "main_sha": "a" * 40,
        "captured_at": "2026-10-08T00:59:00Z",
        "gates": [
            {"number": 580, "state": "closed", "release_confirmed": True},
            {"number": 1089, "state": "merged", "release_confirmed": True},
        ],
    }

class SnapshotGateTests(unittest.TestCase):
    def test_valid_snapshot_allows_review_not_deploy(self):
        actual = gate.assess(sample(), NOW)
        self.assertTrue(actual["eligible_for_review"])
        self.assertFalse(actual["deploy_authorized"])
        self.assertFalse(actual["merge_authorized"])
        self.assertFalse(actual["mutation_performed"])

    def test_open_gate_is_rejected(self):
        value = sample()
        value["gates"][1]["state"] = "open"
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

    def test_missing_gate_is_rejected(self):
        value = sample()
        value["gates"].pop()
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

    def test_retired_gate_is_rejected(self):
        value = sample()
        value["gates"][1]["number"] = 576
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

    def test_boolean_gate_number_is_rejected(self):
        value = sample()
        value["gates"][1]["number"] = True
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

    def test_old_snapshot_is_rejected(self):
        value = sample()
        value["captured_at"] = "2026-10-07T23:00:00Z"
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

    def test_release_confirmation_required(self):
        value = sample()
        value["gates"][0]["release_confirmed"] = False
        self.assertFalse(gate.assess(value, NOW)["eligible_for_review"])

if __name__ == "__main__":
    unittest.main()
