import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_firefox_supervisor.py"
SPEC = importlib.util.spec_from_file_location("firefox_supervisor", MODULE_PATH)
supervisor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(supervisor)


class FirefoxSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state.json"
        self.original_state = supervisor.STATE
        supervisor.STATE = self.state

    def tearDown(self):
        supervisor.STATE = self.original_state
        self.temp.cleanup()

    def test_db_failure_falls_back_to_runner_targets_api(self):
        with mock.patch.object(supervisor, "allocated_workers_from_db", return_value=(None, "PermissionError: denied")), \
             mock.patch.object(supervisor, "allocated_workers_from_api", return_value=(8, None)):
            count, source, error = supervisor.allocation_state()

        self.assertEqual(8, count)
        self.assertEqual("api", source)
        self.assertIn("PermissionError", error)

    def test_total_allocation_state_failure_does_not_claim_idle(self):
        with mock.patch.object(supervisor, "allocation_state", return_value=(None, "unavailable", "db+api down")), \
             mock.patch.object(supervisor, "firefox_pids", return_value=[]), \
             mock.patch.object(supervisor, "restart_firefox") as restart:
            result = supervisor.run_once(now=100)

        self.assertFalse(result["ok"])
        self.assertEqual("allocation-state-unavailable", result["state"])
        restart.assert_not_called()

    def test_live_firefox_normalizes_obsolete_low_priority(self):
        with mock.patch.object(supervisor, "allocation_state", return_value=(8, "sqlite", None)), \
             mock.patch.object(supervisor, "firefox_pids", return_value=[101, 102]), \
             mock.patch.object(supervisor.os, "getpriority", side_effect=[10, 0]), \
             mock.patch.object(supervisor.os, "setpriority") as setpriority:
            result = supervisor.run_once(now=100)

        self.assertEqual("firefox-live", result["state"])
        setpriority.assert_called_once_with(supervisor.os.PRIO_PROCESS, 101, 0)
        self.assertEqual([101], result["priority"]["changed"])

    def test_missing_firefox_requires_two_confirmations_then_restarts(self):
        with mock.patch.object(supervisor, "allocation_state", return_value=(8, "sqlite", None)), \
             mock.patch.object(supervisor, "firefox_pids", return_value=[]), \
             mock.patch.object(supervisor, "restart_firefox", return_value={"ok": True, "status": 200}) as restart:
            first = supervisor.run_once(now=100)
            second = supervisor.run_once(now=106)

        self.assertEqual("firefox-missing-confirming", first["state"])
        self.assertIsNone(first["restart"])
        self.assertEqual("restart-requested", second["state"])
        restart.assert_called_once()

    def test_restart_cooldown_prevents_loop(self):
        self.state.write_text('{"missing_confirmations": 1, "last_restart_at": 90}\n')
        with mock.patch.object(supervisor, "allocation_state", return_value=(8, "sqlite", None)), \
             mock.patch.object(supervisor, "firefox_pids", return_value=[]), \
             mock.patch.object(supervisor, "restart_firefox") as restart:
            result = supervisor.run_once(now=100)

        self.assertEqual("firefox-missing-confirming", result["state"])
        restart.assert_not_called()

    def test_no_allocations_does_not_manage_firefox(self):
        with mock.patch.object(supervisor, "allocation_state", return_value=(0, "sqlite", None)), \
             mock.patch.object(supervisor, "firefox_pids", return_value=[]) as pids, \
             mock.patch.object(supervisor, "restart_firefox") as restart:
            result = supervisor.run_once(now=100)

        self.assertEqual("idle-no-allocations", result["state"])
        pids.assert_called_once()
        restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
