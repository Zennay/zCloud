from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_ftmo_research_runner_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("ftmo_runner_diag", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FtmoResearchRunnerDiagnosticTests(unittest.TestCase):
    def test_parse_runner_units_filters_and_sorts(self):
        text = """
actions.runner.Zennay-zCloud.zcloud-vps-1.service loaded active running zCloud
garbage.service loaded active running nope
actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service loaded active running FTMO
"""
        self.assertEqual(
            [
                "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
                "actions.runner.Zennay-zCloud.zcloud-vps-1.service",
            ],
            MODULE.parse_runner_units(text),
        )

    def test_parse_show_keeps_only_bounded_fields(self):
        parsed = MODULE.parse_show(
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=123\n"
            "ControlGroup=/system.slice/actions.runner.example.service\n"
            "ExecMainStatus=0\n"
            "NRestarts=2\n"
            "Environment=SECRET=should-not-leak\n"
        )
        self.assertNotIn("Environment", parsed)
        self.assertEqual("active", parsed["ActiveState"])
        self.assertEqual("2", parsed["NRestarts"])

    def test_classify_idle(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {
                "ActiveState": "active",
                "SubState": "running",
                "ExecMainStatus": "0",
                "NRestarts": "0",
            },
            listeners=1,
            workers=0,
        )
        self.assertEqual("idle", result["status"])
        self.assertEqual("runner_ready", result["recovery_advice"])
        self.assertEqual(1, result["listener_count"])
        self.assertEqual(0, result["worker_count"])

    def test_classify_busy(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {"ActiveState": "active", "SubState": "running", "NRestarts": "0"},
            listeners=1,
            workers=1,
        )
        self.assertEqual("busy", result["status"])
        self.assertEqual("leave_inflight_work_untouched", result["recovery_advice"])

    def test_classify_degraded_when_listener_count_is_wrong(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {"ActiveState": "active", "SubState": "running", "NRestarts": "0"},
            listeners=0,
            workers=0,
        )
        self.assertEqual("degraded", result["status"])
        self.assertEqual("inspect_listener_state", result["recovery_advice"])

    def test_classify_offline_when_service_is_not_active_and_no_processes_remain(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {"ActiveState": "inactive", "SubState": "dead", "NRestarts": "1"},
            listeners=0,
            workers=0,
        )
        self.assertEqual("offline", result["status"])
        self.assertEqual("service_start_candidate", result["recovery_advice"])

    def test_inactive_service_with_listener_fails_closed_as_orphaned(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {"ActiveState": "inactive", "SubState": "dead", "NRestarts": "0"},
            listeners=1,
            workers=0,
        )
        self.assertEqual("orphaned_processes", result["status"])
        self.assertEqual(
            "reconcile_orphans_before_service_start",
            result["recovery_advice"],
        )

    def test_inactive_service_with_worker_fails_closed_as_orphaned(self):
        result = MODULE.classify_unit(
            "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
            {"ActiveState": "inactive", "SubState": "dead", "NRestarts": "0"},
            listeners=0,
            workers=1,
        )
        self.assertEqual("orphaned_processes", result["status"])
        self.assertEqual(
            "reconcile_orphans_before_service_start",
            result["recovery_advice"],
        )

    def test_unit_inventory_is_bounded(self):
        lines = [
            f"actions.runner.Zennay-Ftmo.runner-{idx}.service loaded active running runner"
            for idx in range(MODULE.MAX_UNITS + 1)
        ]
        with self.assertRaisesRegex(RuntimeError, "bounded limit"):
            MODULE.parse_runner_units("\n".join(lines))


if __name__ == "__main__":
    unittest.main()
