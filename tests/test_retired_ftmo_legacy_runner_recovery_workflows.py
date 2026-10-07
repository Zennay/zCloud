from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-main27ed-runner-recovery.yml",
    "ftmo-worker1-unblock-main-verify.yml",
    "ftmo-main5335-pr471-queue-recovery.yml",
    "recover-ftmo-listener-pr509.yml",
)

TERMINAL_MARKERS = (
    "FTMO_MAIN27ED_SCHEDULING_GREEN",
    "FTMO_CURRENT_MAIN_SCHEDULING_GREEN",
    "FTMO_MAIN5335_SCHEDULING_GREEN",
    "FTMO_PR509_ZCLOUD_RECOVERY_GREEN",
)


class RetiredFtmoLegacyRunnerRecoveryWorkflowsTests(unittest.TestCase):
    def test_legacy_runner_recovery_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_legacy_recovery_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
