"""Focused fail-closed tests for an offline sequence continuity diagnostic."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/control_plane_sequence_gap_offline_20261009_w1.py"
spec = importlib.util.spec_from_file_location("sequence_gap_offline", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SequenceGapTests(unittest.TestCase):
    def check(self, events, **kwargs):
        return module.inspect_sequence(events, expected_worker=kwargs.get("worker", "worker-1"),
                                       expected_generation=kwargs.get("generation", "gen-2"),
                                       last_sequence=kwargs.get("cursor", 4))
    def event(self, n, worker="worker-1", generation="gen-2"):
        return {"worker": worker, "generation": generation, "sequence": n}
    def test_contiguous_batch_diagnostic_never_authorizes(self):
        self.assertEqual(self.check([self.event(5), self.event(6)]),
                         {"continuous": True, "reason": "continuous_offline_only",
                          "last_sequence": 6, "authorizes_action": False})
    def test_gap(self):
        self.assertEqual(self.check([self.event(6)])["reason"], "sequence_gap_or_replay")
    def test_duplicate(self):
        self.assertEqual(self.check([self.event(5), self.event(5)])["reason"], "sequence_gap_or_replay")
    def test_reverse_order(self):
        self.assertFalse(self.check([self.event(6), self.event(5)])["continuous"])
    def test_old_replay(self):
        self.assertFalse(self.check([self.event(4)])["continuous"])
    def test_identity_mismatch(self):
        self.assertEqual(self.check([self.event(5, worker="other")])["reason"], "identity_mismatch")
    def test_generation_mismatch(self):
        self.assertEqual(self.check([self.event(5, generation="gen-1")])["reason"], "identity_mismatch")
    def test_extra_field_rejected(self):
        self.assertEqual(self.check([{**self.event(5), "approved": True}])["reason"], "invalid_event")
    def test_bool_sequence_rejected(self):
        self.assertEqual(self.check([self.event(True)])["reason"], "invalid_sequence")
    def test_float_sequence_rejected(self):
        self.assertEqual(self.check([self.event(5.0)])["reason"], "invalid_sequence")
    def test_overflow_rejected(self):
        self.assertEqual(self.check([self.event(2**63)])["reason"], "invalid_sequence")
    def test_invalid_cursor_rejected(self):
        self.assertEqual(self.check([self.event(5)], cursor=True)["reason"], "invalid_cursor")
    def test_unicode_confusable_identity_rejected(self):
        self.assertEqual(self.check([self.event(5)], worker="wоrker")["reason"], "invalid_identity")
    def test_empty_batch_rejected(self):
        self.assertEqual(self.check([])["reason"], "invalid_batch")
    def test_unbounded_batch_rejected(self):
        self.assertEqual(self.check([self.event(5)] * 1001)["reason"], "invalid_batch")
    def test_malformed_event_rejected(self):
        self.assertEqual(self.check(["event"])["reason"], "invalid_event")
    def test_cursor_upper_bound_cannot_advance(self):
        self.assertEqual(self.check([self.event(2**63)], cursor=2**63-1)["reason"], "invalid_sequence")

    def test_custom_mapping_not_executed(self):
        class HostileMapping(dict):
            def __iter__(self):
                raise RuntimeError("untrusted iteration")
        self.assertEqual(self.check([HostileMapping(self.event(5))])["reason"], "invalid_event")
    def test_custom_list_not_executed(self):
        class HostileList(list):
            def __iter__(self):
                raise RuntimeError("untrusted iteration")
        self.assertEqual(self.check(HostileList([self.event(5)]))["reason"], "invalid_batch")
    def test_dict_subclass_rejected(self):
        class CustomDict(dict):
            pass
        self.assertEqual(self.check([CustomDict(self.event(5))])["reason"], "invalid_event")
    def test_sequence_zero_from_initial_cursor(self):
        self.assertTrue(self.check([self.event(1)], cursor=0)["continuous"])
    def test_no_empty_identifier(self):
        self.assertEqual(self.check([self.event(5)], worker="")["reason"], "invalid_identity")

if __name__ == "__main__":
    unittest.main()
