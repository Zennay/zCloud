"""Focused tests for an offline, non-authorizing event-order reference."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from control_plane_causal_order_offline_20261009_w1 import validate_sequence


def sample(*events):
    return [{"run_id": "worker-1", "sequence": i, "event": event}
            for i, event in enumerate(events)]


class CausalOrderTests(unittest.TestCase):
    def test_success(self):
        self.assertEqual(validate_sequence(sample("requested", "admitted", "started", "completed")),
                         (True, "valid_offline_only"))

    def test_failure_terminal(self):
        self.assertTrue(validate_sequence(sample("requested", "admitted", "started", "failed"))[0])

    def test_cancel_while_queued(self):
        self.assertTrue(validate_sequence(sample("requested", "cancelled"))[0])

    def test_missing_sequence(self):
        rows = sample("requested", "admitted", "started", "completed")
        rows[2]["sequence"] = 3
        self.assertFalse(validate_sequence(rows)[0])

    def test_duplicate_sequence(self):
        rows = sample("requested", "admitted")
        rows[1]["sequence"] = 0
        self.assertFalse(validate_sequence(rows)[0])

    def test_mixed_ids(self):
        rows = sample("requested", "cancelled")
        rows[1]["run_id"] = "another"
        self.assertFalse(validate_sequence(rows)[0])

    def test_illegal_jump(self):
        self.assertFalse(validate_sequence(sample("requested", "completed"))[0])

    def test_terminal_replay(self):
        self.assertFalse(validate_sequence(sample("requested", "cancelled", "cancelled"))[0])

    def test_missing_terminal(self):
        self.assertFalse(validate_sequence(sample("requested", "admitted"))[0])

    def test_bool_sequence(self):
        rows = sample("requested", "cancelled")
        rows[0]["sequence"] = False
        self.assertFalse(validate_sequence(rows)[0])

    def test_unknown_keys(self):
        rows = sample("requested", "cancelled")
        rows[0]["authorization"] = True
        self.assertFalse(validate_sequence(rows)[0])

    def test_malformed_input(self):
        for value in (None, {}, [], "requested", [None], [1]):
            with self.subTest(value=value):
                self.assertFalse(validate_sequence(value)[0])

    def test_non_ascii_identifier(self):
        rows = sample("requested", "cancelled")
        rows[0]["run_id"] = "wοrker"
        self.assertFalse(validate_sequence(rows)[0])

    def test_unknown_event(self):
        self.assertFalse(validate_sequence(sample("requested", "privileged"))[0])


if __name__ == "__main__":
    unittest.main()
