from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-stable-root-permission-repair.yml",
    "ftmo-stable-runtime-permission-repair-e536.yml",
)

TERMINAL_MARKERS = (
    "FTMO_REPAIRED_SYNC_DISPATCHED",
    "FTMO_REPAIRED_RUNTIME_VERIFY_DISPATCHED",
    "FTMO_POST_REPAIR_GATES_DISPATCHED",
)


class RetiredFtmoE536PermissionRepairWorkflowsTests(unittest.TestCase):
    def test_e536_permission_repair_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_repair_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
