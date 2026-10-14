from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED_WORKFLOW = WORKFLOWS / "lightup-ftmo-listener-recovery.yml"
RETIRED_TEST = ROOT / "tests" / "test_lightup_ftmo_listener_recovery.py"


class RetiredLightUpFtmoListenerRecoveryWorkflowTests(unittest.TestCase):
    def test_terminal_listener_recovery_paths_are_absent(self) -> None:
        self.assertFalse(
            RETIRED_WORKFLOW.exists(),
            "terminal LightUp ST3 FTMO listener recovery must stay retired",
        )
        self.assertFalse(
            RETIRED_TEST.exists(),
            "obsolete workflow-specific recovery test must stay retired",
        )

    def test_terminal_listener_recovery_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Recover FTMO listener for LightUp ST3 proof",
            "lightup-ftmo-listener-recovery",
            'LIGHTUP_RUN_ID: "37314597756"',
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
