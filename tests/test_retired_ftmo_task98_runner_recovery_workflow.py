from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-task98-runner-recovery-20261004.yml"


class RetiredFtmoTask98RunnerRecoveryWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "obsolete FTMO TASK-98/TASK-99 runner-recovery carrier must stay retired",
        )

    def test_task98_recovery_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Recover FTMO runner after TASK-98 host-memory deferral",
            "ftmo-task98-runner-recovery-20261004",
            "FTMO_TASK98_RECOVERY_ACTION",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for pattern in ("*.yml", "*.yaml")
            for path in sorted(WORKFLOWS.glob(pattern))
        )
        for marker in markers:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
