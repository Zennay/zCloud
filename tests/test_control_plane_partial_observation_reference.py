"""Offline executable examples for the control-plane partial-observation contract.

No network, queue, runtime imports, or privileged side effects.
"""
from dataclasses import dataclass
import unittest


@dataclass(frozen=True)
class Observation:
    source: str
    subject: str
    cursor: int
    observed_at: int
    values: dict


def apply(previous: dict, incoming: Observation) -> dict:
    """Merge only named fields from a validated same-source observation.

    Keys in previous are (source, subject, field); values are (value, timestamp, cursor).
    Cross-source claims never overwrite each other.
    """
    if (not isinstance(incoming.source, str) or not incoming.source.strip()
            or not isinstance(incoming.subject, str) or not incoming.subject.strip()
            or type(incoming.cursor) is not int or incoming.cursor < 0
            or type(incoming.observed_at) is not int or incoming.observed_at < 0
            or not isinstance(incoming.values, dict)):
        raise ValueError("ambiguous observation identity, cursor, clock, or fields")
    # A source/subject cursor is a snapshot identity, not a per-field revision.
    # Reject stale additions and contradictory snapshot clocks even for new fields.
    matching = [entry for (source, subject, _), entry in previous.items()
                if source == incoming.source and subject == incoming.subject]
    if matching:
        latest_cursor = max(entry[2] for entry in matching)
        if incoming.cursor < latest_cursor:
            return dict(previous)
        latest_times = {entry[1] for entry in matching if entry[2] == latest_cursor}
        if incoming.cursor == latest_cursor and latest_times != {incoming.observed_at}:
            raise ValueError("same-cursor snapshot timestamp conflict")
        if incoming.cursor > latest_cursor and incoming.observed_at < max(latest_times):
            raise ValueError("snapshot timestamp regression")
    result = dict(previous)
    for field, value in incoming.values.items():
        if not isinstance(field, str) or not field:
            raise ValueError("invalid field")
        key = (incoming.source, incoming.subject, field)
        old = result.get(key)
        if old is not None:
            if incoming.cursor > old[2] and incoming.observed_at < old[1]:
                raise ValueError("same-source timestamp regression")
            if incoming.cursor < old[2]:
                continue
            if incoming.cursor == old[2] and (value, incoming.observed_at) != old[:2]:
                raise ValueError("contradictory same-cursor observation")
        result[key] = (value, incoming.observed_at, incoming.cursor)
    return result


def action_authorized(_observations: dict) -> bool:
    """Display observations are never authority for a privileged action."""
    return False


class PartialObservationContractTests(unittest.TestCase):
    def test_missing_field_does_not_inherit_new_timestamp(self):
        old = apply({}, Observation("vps", "worker-1", 1, 100, {"heartbeat": "alive", "cpu": 50}))
        updated = apply(old, Observation("vps", "worker-1", 2, 200, {"cpu": 60}))
        self.assertEqual(updated[("vps", "worker-1", "heartbeat")], ("alive", 100, 1))
        self.assertEqual(updated[("vps", "worker-1", "cpu")], (60, 200, 2))

    def test_false_and_zero_are_present_not_missing(self):
        result = apply({}, Observation("sqlite", "queue", 1, 100, {"depth": 0, "enabled": False}))
        self.assertEqual(result[("sqlite", "queue", "depth")][0], 0)
        self.assertIs(result[("sqlite", "queue", "enabled")][0], False)

    def test_old_cursor_cannot_overwrite_newer_evidence(self):
        current = apply({}, Observation("vps", "worker-1", 3, 300, {"heartbeat": "alive"}))
        replayed = apply(current, Observation("vps", "worker-1", 2, 200, {"heartbeat": "dead"}))
        self.assertEqual(replayed, current)

    def test_same_cursor_conflict_rejected(self):
        old = apply({}, Observation("vps", "worker-1", 1, 100, {"state": "running"}))
        with self.assertRaises(ValueError):
            apply(old, Observation("vps", "worker-1", 1, 100, {"state": "stopped"}))

    def test_independent_source_order_keeps_both_provenances(self):
        a = Observation("browser", "worker-1", 1, 100, {"state": "running"})
        b = Observation("sqlite", "worker-1", 7, 95, {"state": "pending"})
        self.assertEqual(apply(apply({}, a), b), apply(apply({}, b), a))

    def test_observation_does_not_grant_mutation_authority(self):
        states = apply({}, Observation("github", "main", 1, 100, {"checks": "success"}))
        self.assertFalse(action_authorized(states))

    def test_higher_cursor_with_regressed_observation_time_rejected(self):
        old = apply({}, Observation("vps", "worker-1", 1, 100, {"state": "running"}))
        with self.assertRaises(ValueError):
            apply(old, Observation("vps", "worker-1", 2, 90, {"state": "stopped"}))

    def test_boolean_cursor_is_not_valid_sequence_identity(self):
        with self.assertRaises(ValueError):
            apply({}, Observation("vps", "worker-1", True, 100, {"state": "running"}))

    def test_boolean_clock_is_not_valid_timestamp(self):
        with self.assertRaises(ValueError):
            apply({}, Observation("vps", "worker-1", 1, False, {"state": "running"}))

    def test_none_field_map_rejected(self):
        with self.assertRaises(ValueError):
            apply({}, Observation("vps", "worker-1", 1, 100, None))

    def test_stale_cursor_cannot_introduce_new_field(self):
        latest = apply({}, Observation("vps", "worker-1", 4, 400, {"cpu": 60}))
        self.assertEqual(apply(latest, Observation("vps", "worker-1", 3, 300, {"heartbeat": "dead"})), latest)

    def test_same_snapshot_cursor_requires_consistent_clock_across_fields(self):
        old = apply({}, Observation("vps", "worker-1", 4, 400, {"cpu": 60}))
        with self.assertRaises(ValueError):
            apply(old, Observation("vps", "worker-1", 4, 401, {"heartbeat": "alive"}))

    def test_new_field_cannot_hide_source_clock_regression(self):
        old = apply({}, Observation("vps", "worker-1", 4, 400, {"cpu": 60}))
        with self.assertRaises(ValueError):
            apply(old, Observation("vps", "worker-1", 5, 399, {"heartbeat": "alive"}))

    def test_missing_identity_rejected(self):
        with self.assertRaises(ValueError):
            apply({}, Observation("", "worker-1", 1, 100, {"state": "running"}))


if __name__ == "__main__":
    unittest.main()
