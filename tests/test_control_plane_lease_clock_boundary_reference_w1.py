"""Offline, non-authorizing reference checks for lease clock boundaries.

This model is intentionally independent of zCloud's production claim implementation.
It documents time arithmetic failure modes for integration-owner review only.
"""
import unittest
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
MAX_LEASE_SECONDS = 3600


def canonical_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("invalid timezone offset")
    return value.astimezone(UTC)


def lease_deadline(now, seconds):
    now = canonical_utc(now)
    if type(seconds) is not int or not 1 <= seconds <= MAX_LEASE_SECONDS:
        raise ValueError("invalid lease duration")
    return now + timedelta(seconds=seconds)


def lease_is_live(deadline, now):
    deadline = canonical_utc(deadline)
    now = canonical_utc(now)
    return now < deadline


class LeaseClockBoundaryReferenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)

    def test_expiry_is_exclusive(self):
        deadline = lease_deadline(self.now, 60)
        self.assertTrue(lease_is_live(deadline, self.now + timedelta(seconds=59, microseconds=999999)))
        self.assertFalse(lease_is_live(deadline, deadline))
        self.assertFalse(lease_is_live(deadline, deadline + timedelta(microseconds=1)))

    def test_utc_equivalent_offsets(self):
        plus_two = timezone(timedelta(hours=2))
        same_instant = self.now.astimezone(plus_two)
        self.assertEqual(lease_deadline(same_instant, 60), lease_deadline(self.now, 60))
        self.assertTrue(lease_is_live(lease_deadline(self.now, 60), same_instant))

    def test_cross_midnight_and_year(self):
        start = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
        self.assertEqual(lease_deadline(start, 2), datetime(2027, 1, 1, 0, 0, 1, tzinfo=UTC))

    def test_naive_datetime_denied(self):
        with self.assertRaises(ValueError):
            lease_deadline(datetime(2026, 10, 9, 2), 60)
        with self.assertRaises(ValueError):
            lease_is_live(self.now, datetime(2026, 10, 9, 2))

    def test_boolean_float_zero_negative_and_excessive_duration_denied(self):
        for seconds in (True, False, 1.0, "60", 0, -1, 3601, None):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    lease_deadline(self.now, seconds)

    def test_max_duration_permitted(self):
        self.assertEqual(lease_deadline(self.now, MAX_LEASE_SECONDS),
                         self.now + timedelta(hours=1))

    def test_non_datetime_denied(self):
        for value in ("2026-10-09T02:00:00Z", 0, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_utc(value)


if __name__ == "__main__":
    unittest.main()
