from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-task125-unit-lockstep-recovery.yml"


class FtmoTask125RecoveryRetirementTests(unittest.TestCase):
    def test_retired_workflow_stays_absent(self):
        self.assertFalse(
            RETIRED.exists(),
            "historical FTMO TASK-125 live recovery authority must stay retired",
        )

    def test_live_recovery_markers_are_not_reintroduced_elsewhere(self):
        forbidden = (
            "name: FTMO TASK-125 unit lockstep recovery",
            "group: ftmo-task125-unit-lockstep-recovery-v4",
            "FTMO_TASK125_UNIT_LOCKSTEP=verified",
            "FTMO_TASK125_DEPENDENCY_INSTALLED=",
        )
        hits = []
        workflow_files = sorted(set(WORKFLOWS.glob("*.yml")) | set(WORKFLOWS.glob("*.yaml")))
        for path in workflow_files:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    hits.append(f"{path.name}: {marker}")
        self.assertEqual([], hits, "retired recovery authority was reintroduced")


if __name__ == "__main__":
    unittest.main()
