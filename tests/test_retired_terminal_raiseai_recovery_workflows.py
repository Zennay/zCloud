from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    "raiseai-pr24-runner-recovery-20261004.yml",
    "raiseai-pr25-runner-recovery-20261004.yml",
    "raiseai-pr31-runner-recovery-20261004.yml",
    "raiseai-main-v153-runner-recovery.yml",
)


class RetiredTerminalRaiseAIRecoveryWorkflowTests(unittest.TestCase):
    def test_retired_raiseai_recovery_paths_are_absent(self) -> None:
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), f"{name} must stay retired")

    def test_terminal_raiseai_recovery_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Legacy pinned RaiseAI runner recovery (manual only)",
            "Legacy RaiseAI runner recovery (manual only)",
            "Recover RaiseAI runner for current PR31 exact-head",
            "Recover RaiseAI PR71 runner",
            "RAISEAI_PR31_IDENTITY",
            "RAISEAI_PR71 status=",
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
