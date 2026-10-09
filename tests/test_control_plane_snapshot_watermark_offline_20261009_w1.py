"""Isolated tests for the non-authorizing offline snapshot watermark comparator."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from control_plane_snapshot_watermark_offline_20261009_w1 import Watermark, compare

SHA = "a" * 40
OTHER = "b" * 40
TS = "2026-10-09T01:00:00Z"


def mark(g=2, s=5, head=SHA, at=TS):
    return Watermark(g, s, head, at)


class WatermarkTests(unittest.TestCase):
    def test_forward(self):
        self.assertEqual(compare(mark(), mark(s=6)), "forward")

    def test_duplicate(self):
        self.assertEqual(compare(mark(), mark()), "duplicate")

    def test_same_sequence_different_receipt(self):
        self.assertEqual(compare(mark(), mark(at="2026-10-09T01:01:00Z")), "conflicting_receipt")

    def test_old_generation(self):
        self.assertEqual(compare(mark(), mark(g=1)), "stale_generation")

    def test_old_sequence(self):
        self.assertEqual(compare(mark(), mark(s=4)), "stale_sequence")

    def test_changed_head_same_generation(self):
        self.assertEqual(compare(mark(), mark(s=6, head=OTHER)), "conflicting_head")

    def test_new_generation_requires_zero_sequence(self):
        self.assertEqual(compare(mark(), mark(g=3, s=1)), "unanchored_generation")

    def test_new_generation_is_not_authorized(self):
        self.assertEqual(compare(mark(), mark(g=3, s=0, head=OTHER)), "new_generation_unverified")

    def test_bool_is_not_counter(self):
        self.assertEqual(compare(mark(), mark(g=True)), "invalid")

    def test_negative_and_overflow_counters(self):
        self.assertEqual(compare(mark(), mark(s=-1)), "invalid")
        self.assertEqual(compare(mark(), mark(s=2**63)), "invalid")

    def test_bad_sha(self):
        self.assertEqual(compare(mark(), mark(head="A" * 40)), "invalid")

    def test_bad_timestamp(self):
        self.assertEqual(compare(mark(), mark(at="2026-02-30T01:00:00Z")), "invalid")
        self.assertEqual(compare(mark(), mark(at="2026-10-09T01:00:00+01:00")), "invalid")

    def test_invalid_input(self):
        self.assertEqual(compare({}, mark()), "invalid")
        self.assertEqual(compare(mark(), None), "invalid")


if __name__ == "__main__":
    unittest.main()
