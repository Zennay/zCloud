"""Offline-only negative matrix for restart admission."""
import unittest

from scripts.control_plane_restart_admission_offline_20261009_w1 import assess_restart


def valid():
    return dict(worker_id="worker-1", generation_id="gen-9",
                observed_generation_id="gen-9", observation_age_seconds=4,
                worker_active=False, action_in_flight=False,
                browser_healthy=True, memory_safe=True, lease_owned=True,
                restart_authorized=True)


class RestartAdmissionTests(unittest.TestCase):
    def test_positive_is_offline_only(self):
        self.assertEqual(assess_restart(valid()).reason, "admitted_offline_only")

    def test_missing_fields_fail_closed(self):
        for key in valid():
            with self.subTest(key=key):
                sample = valid()
                del sample[key]
                self.assertFalse(assess_restart(sample).allowed)

    def test_stale_and_malformed_age(self):
        for age in (-1, 31, None, True, "0", float("nan"), float("inf")):
            with self.subTest(age=age):
                sample = valid()
                sample["observation_age_seconds"] = age
                self.assertFalse(assess_restart(sample).allowed)

    def test_inflight_and_active_never_restarted(self):
        for flag in ("worker_active", "action_in_flight"):
            sample = valid()
            sample[flag] = True
            self.assertEqual(assess_restart(sample).reason, "already_running")

    def test_required_guards(self):
        for flag in ("browser_healthy", "memory_safe", "lease_owned",
                     "restart_authorized"):
            sample = valid()
            sample[flag] = False
            self.assertFalse(assess_restart(sample).allowed)

    def test_generation_binding(self):
        sample = valid()
        sample["observed_generation_id"] = "previous"
        self.assertEqual(assess_restart(sample).reason, "generation_mismatch")

    def test_unknown_flags_are_not_truthy(self):
        for flag in ("worker_active", "action_in_flight", "browser_healthy",
                     "memory_safe", "lease_owned", "restart_authorized"):
            sample = valid()
            sample[flag] = "false"
            self.assertEqual(assess_restart(sample).reason, "invalid_flags")


if __name__ == "__main__":
    unittest.main()
