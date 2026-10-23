"""Offline safety contracts for the fail-closed Actions listener recovery helper."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/zcloud_idle_actions_listener_recovery.py"
spec = importlib.util.spec_from_file_location("listener_rescue", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class RecoveryTests(unittest.TestCase):
    def test_active_listeners_are_untouched(self):
        for state in ("active", "activating", "reloading"):
            self.assertEqual("preserve_existing_runner", mod.decide("loaded", state, 0))

    def test_orphan_or_running_worker_denied(self):
        for state in ("inactive", "failed"):
            self.assertEqual("deny_residual_runner_processes", mod.decide("loaded", state, 1))

    def test_missing_or_unknown_unit_denied(self):
        self.assertEqual("deny_missing_unit", mod.decide("not-found", "inactive", 0))
        self.assertEqual("deny_unknown_service_state", mod.decide("loaded", "deactivating", 0))

    def test_only_unoccupied_offline_runner_is_eligible(self):
        for state in ("inactive", "failed"):
            self.assertEqual("eligible_start_inactive_listener", mod.decide("loaded", state, 0))

    def test_dry_run_never_mutates(self):
        with patch.object(mod, "service_state", return_value=("loaded", "inactive")), \
             patch.object(mod, "runner_process_count", return_value=0), \
             patch.object(mod.subprocess, "run") as run:
            report = mod.rescue("ftmo", False)
            self.assertEqual("eligible_start_inactive_listener", report["decision"])
            self.assertFalse(report["mutation_performed"])
            run.assert_not_called()

    def test_apply_refuses_when_process_appears(self):
        with patch.object(mod, "service_state", return_value=("loaded", "inactive")), \
             patch.object(mod, "runner_process_count", side_effect=[0, 1]), \
             patch.object(mod.subprocess, "run") as run:
            report = mod.rescue("zcloud", True)
            self.assertEqual("deny_state_changed", report["decision"])
            run.assert_not_called()

    def test_apply_only_starts_whitelisted_inactive_unit(self):
        with patch.object(mod, "service_state", side_effect=[("loaded", "inactive"), ("loaded", "inactive"), ("loaded", "active")]), \
             patch.object(mod, "runner_process_count", side_effect=[0, 0]), \
             patch.object(mod.subprocess, "run") as run:
            report = mod.rescue("ftmo", True)
            self.assertEqual("started_inactive_listener", report["decision"])
            self.assertTrue(report["mutation_performed"])
            cmd = run.call_args.args[0]
            self.assertEqual(["sudo", "-n", "systemctl", "start", mod.UNITS["ftmo"][0]], cmd)

    def test_v2_retirement_keeps_runner_and_memory_self_heal(self):
        retirement = (PATH.parents[1] / "scripts/zcloud_retire_legacy_worker_services.sh").read_text()
        self.assertNotIn("retire system zcloud-self-heal", retirement)
        self.assertIn("retire user chatgpt-runner-watchdog", retirement)
        self.assertIn("retire user zcloud-watchdog", retirement)
        self.assertIn("zcloud-firefox-supervisor.timer", retirement)

    def test_repository_recovery_scope_is_narrow(self):
        text = PATH.read_text()
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("kill -", text)
        self.assertNotIn("firefox.service", text)
        self.assertNotIn("history.db", text)


if __name__ == "__main__":
    unittest.main()
