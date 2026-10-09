"""Offline contract: evidence freshness must use timezone-aware timestamps.

Run: python -m unittest tests/test_control_plane_evidence_timezone_boundary_20261009_w3.py
This fixture never loads zCloud runtime, a worker queue or production credentials.
It documents a proposed acceptance boundary; it does not authorize any live action.
"""
import unittest
from datetime import datetime, timedelta, timezone


def evidence_is_fresh(evidence_at: str, now_at: str, max_age_seconds: int = 300) -> bool:
    """Reject ambiguous, future-dated and stale observations, without trusting local TZ."""
    if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool) or max_age_seconds < 0:
        return False

    def parse(value: str):
        if not isinstance(value, str):
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc)

    observed, now = parse(evidence_at), parse(now_at)
    if observed is None or now is None:
        return False
    delta = (now - observed).total_seconds()
    return 0 <= delta <= max_age_seconds


class EvidenceTimezoneBoundary(unittest.TestCase):
    def test_same_instant_different_offsets_is_valid(self):
        self.assertTrue(evidence_is_fresh("2026-10-09T05:30:00+02:00", "2026-10-09T03:30:00Z"))

    def test_dst_fall_back_does_not_reverse_utc_order(self):
        self.assertTrue(evidence_is_fresh("2026-10-25T02:58:00+02:00", "2026-10-25T02:02:00+01:00"))

    def test_future_evidence_denied(self):
        self.assertFalse(evidence_is_fresh("2026-10-09T03:30:01Z", "2026-10-09T03:30:00Z"))

    def test_stale_evidence_denied(self):
        self.assertFalse(evidence_is_fresh("2026-10-09T03:24:59Z", "2026-10-09T03:30:00Z"))

    def test_exact_boundary_allowed(self):
        self.assertTrue(evidence_is_fresh("2026-10-09T03:25:00Z", "2026-10-09T03:30:00Z"))

    def test_unzoned_and_malformed_denied(self):
        for value in ("2026-10-09T03:30:00", "invalid", "", None, 0):
            with self.subTest(value=value):
                self.assertFalse(evidence_is_fresh(value, "2026-10-09T03:30:00Z"))

    def test_invalid_reference_and_age_denied(self):
        self.assertFalse(evidence_is_fresh("2026-10-09T03:30:00Z", "2026-10-09T03:30:00"))
        self.assertFalse(evidence_is_fresh("2026-10-09T03:30:00Z", "2026-10-09T03:30:00Z", -1))
        self.assertFalse(evidence_is_fresh("2026-10-09T03:30:00Z", "2026-10-09T03:30:00Z", True))


if __name__ == "__main__":
    unittest.main()
