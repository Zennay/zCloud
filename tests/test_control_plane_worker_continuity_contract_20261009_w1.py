"""Offline, deny-first continuity evidence classifier; never mutates workers.

This is a fixture-only reference contract for #1218 / PR #1249, not runtime
admission. Timestamps are integer seconds from an authoritative snapshot clock.
"""
from dataclasses import dataclass
from typing import Optional
import unittest


@dataclass(frozen=True)
class Evidence:
    observed_at: int
    configured_slots: int
    generation_started: tuple[str, ...] = ()
    recent_progress: tuple[str, ...] = ()
    prompt_sent_at: Optional[int] = None
    resource_observed_at: Optional[int] = None
    memory_pressure: bool = False
    heartbeat_observed_at: Optional[int] = None
    production_receipt_valid: bool = False


def classify(e: Evidence, *, freshness_seconds: int = 60, watchdog_seconds: int = 120) -> dict:
    """Read-only decision; empty/old evidence can never admit more workers."""
    if e.observed_at < 0 or e.configured_slots < 0:
        raise ValueError("invalid snapshot")
    active = set(e.generation_started) & set(e.recent_progress)
    active_count = len(active)
    heartbeat_fresh = (
        e.heartbeat_observed_at is not None
        and 0 <= e.observed_at - e.heartbeat_observed_at <= freshness_seconds
    )
    capacity_fresh = (
        e.resource_observed_at is not None
        and 0 <= e.observed_at - e.resource_observed_at <= freshness_seconds
    )
    capacity = ("unknown" if not capacity_fresh else
                "constrained" if e.memory_pressure else "observed")
    if active_count:
        state = "healthy" if heartbeat_fresh else "unknown"
    elif e.prompt_sent_at is not None and 0 <= e.observed_at - e.prompt_sent_at < watchdog_seconds:
        state = "pending"
    elif e.prompt_sent_at is not None and e.observed_at - e.prompt_sent_at >= watchdog_seconds:
        state = "suspected_no_generation" if heartbeat_fresh else "unknown"
    else:
        state = "unknown"
    return {
        "active_generations": active_count,
        "configured_slots": e.configured_slots,
        "state": state,
        "capacity": capacity,
        "allow_new_browser": bool(capacity == "observed" and heartbeat_fresh and not active_count),
        "allow_force_push": False,
        "release_verified": e.production_receipt_valid,
    }


class OfflineContinuityContractTests(unittest.TestCase):
    def base(self, **kw):
        values = dict(observed_at=1000, configured_slots=8,
                      resource_observed_at=980, heartbeat_observed_at=980)
        values.update(kw)
        return Evidence(**values)

    def test_three_is_not_eight(self):
        d = classify(self.base(generation_started=("a", "b", "c"),
                               recent_progress=("a", "b", "c")))
        self.assertEqual(d["active_generations"], 3)
        self.assertEqual(d["configured_slots"], 8)
        self.assertFalse(d["allow_new_browser"])

    def test_pending_then_watchdog(self):
        self.assertEqual(classify(self.base(prompt_sent_at=900))["state"], "pending")
        self.assertEqual(classify(self.base(prompt_sent_at=880))["state"],
                         "suspected_no_generation")
        self.assertFalse(classify(self.base(prompt_sent_at=880))["allow_force_push"])

    def test_active_generation_never_force_pushed(self):
        d = classify(self.base(prompt_sent_at=0, generation_started=("a",),
                               recent_progress=("a",)))
        self.assertEqual(d["state"], "healthy")
        self.assertFalse(d["allow_force_push"])

    def test_missing_resource_is_unknown(self):
        d = classify(self.base(resource_observed_at=None))
        self.assertEqual(d["capacity"], "unknown")
        self.assertFalse(d["allow_new_browser"])

    def test_memory_pressure_blocks_browser(self):
        d = classify(self.base(memory_pressure=True))
        self.assertEqual(d["capacity"], "constrained")
        self.assertFalse(d["allow_new_browser"])

    def test_missing_heartbeat_fails_closed(self):
        d = classify(self.base(heartbeat_observed_at=None))
        self.assertEqual(d["state"], "unknown")
        self.assertFalse(d["allow_new_browser"])

    def test_pr_green_without_receipt_not_verified(self):
        self.assertFalse(classify(self.base())["release_verified"])

    def test_future_and_stale_samples_never_count_fresh(self):
        for at in (900, 1001):
            self.assertEqual(classify(self.base(resource_observed_at=at))["capacity"], "unknown")

    def test_duplicate_slot_does_not_inflate_count(self):
        self.assertEqual(classify(self.base(generation_started=("a", "a"),
                                            recent_progress=("a",)))["active_generations"], 1)


if __name__ == "__main__":
    unittest.main()
