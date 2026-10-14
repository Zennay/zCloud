import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_fault_recovery_matrix.py"
SPEC = importlib.util.spec_from_file_location("zcloud_fault_recovery_matrix", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MOD)


class FaultRecoveryMatrixTests(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads((ROOT / "fault-recovery-matrix.v1.json").read_text(encoding="utf-8"))

    def test_matrix_covers_exact_three_roadmap_faults(self):
        report = MOD.validate(self.matrix, ROOT)
        self.assertEqual("valid", report["status"])
        self.assertEqual(
            ["firefox_runtime_loss", "stale_resource_lease", "zcloud_service_restart"],
            report["scenario_ids"],
        )
        self.assertEqual(3, report["scenario_count"])
        self.assertFalse(report["live_mutation"])

    def test_execution_plan_keeps_scenario_id_bound_to_selector(self):
        reordered = copy.deepcopy(self.matrix)
        reordered["scenarios"].reverse()
        report = MOD.validate(reordered, ROOT)
        self.assertEqual(
            [
                (scenario["id"], scenario["selector"])
                for scenario in reordered["scenarios"]
            ],
            [
                (item["id"], item["selector"])
                for item in report["execution_plan"]
            ],
        )

    def test_unknown_scenario_is_fail_closed(self):
        bad = copy.deepcopy(self.matrix)
        bad["scenarios"][0]["id"] = "kill_production_firefox"
        with self.assertRaisesRegex(MOD.MatrixError, "unknown scenario"):
            MOD.validate(bad, ROOT)

    def test_arbitrary_test_selector_is_refused(self):
        bad = copy.deepcopy(self.matrix)
        bad["scenarios"][0]["selector"] = "tests.test_vps_deploy_workflow"
        with self.assertRaisesRegex(MOD.MatrixError, "canonical bounded simulation"):
            MOD.validate(bad, ROOT)

    def test_duplicate_scenario_is_refused(self):
        bad = copy.deepcopy(self.matrix)
        bad["scenarios"][1]["id"] = bad["scenarios"][0]["id"]
        with self.assertRaisesRegex(MOD.MatrixError, "unique canonical strings"):
            MOD.validate(bad, ROOT)

    def test_bool_schema_version_is_refused(self):
        bad = copy.deepcopy(self.matrix)
        bad["schema_version"] = True
        with self.assertRaisesRegex(MOD.MatrixError, "exact integer"):
            MOD.validate(bad, ROOT)

    def test_symlink_matrix_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            real = td / "real.json"
            real.write_text(json.dumps(self.matrix), encoding="utf-8")
            link = td / "matrix.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(MOD.MatrixError, "symlink"):
                MOD._load(link)

    def test_workflow_is_exact_head_non_mutating_permanent_vps(self):
        text = (ROOT / ".github/workflows/zcloud-fault-recovery-matrix-proof.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("zcloud_fault_recovery_matrix.py --execute --require-green", text)
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "sudo ",
            "systemctl restart",
            "systemctl stop",
            "pkill",
            "killall",
            "sqlite3 ",
            "curl -X",
            "gh api --method",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
