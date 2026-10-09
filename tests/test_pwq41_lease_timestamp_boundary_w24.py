"""Offline PWQ-41 command-lease timestamp contract; no production side effects.

This is a proposed boundary test, not evidence that the live scheduler satisfies it.
Run: python3 -m unittest discover -s tests -p 'test_pwq41_lease_timestamp_boundary_w24.py' -v
"""
import unittest
from datetime import datetime, timedelta, timezone

ORDINARY_SECONDS = 300
REPLACEMENT_SECONDS = 900
REPLACEMENT_ACTIONS = frozenset({"new_chat", "drain"})


def stale_pending(action, created_at, now):
    """Deny ambiguous timestamps; never silently interpret naive local time."""
    if not isinstance(action, str) or not isinstance(created_at, str):
        return True
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return True
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return True
    except (ValueError, TypeError, OverflowError):
        return True
    seconds = REPLACEMENT_SECONDS if action.strip().lower() in REPLACEMENT_ACTIONS else ORDINARY_SECONDS
    return (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() >= seconds


class PendingCommandTimestampBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, 4, 19, tzinfo=timezone.utc)

    def stamp(self, seconds):
        return (self.now - timedelta(seconds=seconds)).isoformat()

    def test_replacement_stays_pending_at_301(self):
        self.assertFalse(stale_pending("new_chat", self.stamp(301), self.now))

    def test_replacement_stays_pending_at_899(self):
        self.assertFalse(stale_pending("drain", self.stamp(899), self.now))

    def test_replacement_expires_at_900(self):
        self.assertTrue(stale_pending("new_chat", self.stamp(900), self.now))

    def test_ordinary_expires_at_300(self):
        self.assertTrue(stale_pending("push", self.stamp(300), self.now))

    def test_ordinary_stays_pending_at_299(self):
        self.assertFalse(stale_pending("push", self.stamp(299), self.now))

    def test_case_and_space_normalization(self):
        self.assertFalse(stale_pending(" NEW_CHAT ", self.stamp(301), self.now))

    def test_zulu_timestamp(self):
        self.assertFalse(stale_pending("drain", self.stamp(301).replace("+00:00", "Z"), self.now))

    def test_offset_aware_timestamp(self):
        self.assertFalse(stale_pending("new_chat", "2026-10-08T06:13:59+02:00", self.now))

    def test_naive_timestamp_fails_closed(self):
        self.assertTrue(stale_pending("new_chat", "2026-10-08T04:18:00", self.now))

    def test_invalid_timestamp_fails_closed(self):
        self.assertTrue(stale_pending("new_chat", "invalid", self.now))

    def test_missing_timestamp_fails_closed(self):
        self.assertTrue(stale_pending("new_chat", None, self.now))

    def test_missing_action_fails_closed(self):
        self.assertTrue(stale_pending(None, self.stamp(10), self.now))

    def test_naive_clock_fails_closed(self):
        self.assertTrue(stale_pending("new_chat", self.stamp(10), self.now.replace(tzinfo=None)))

    def test_future_timestamp_preserved(self):
        self.assertFalse(stale_pending("new_chat", self.stamp(-5), self.now))


if __name__ == "__main__":
    unittest.main()
