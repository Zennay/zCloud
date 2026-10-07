from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "zssh-ftmo-runner-recovery-queue-finalize.yml"


class RetiredZsshFtmoVerify72QueueFinalizerTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "terminal zSSH FTMO verify72 queue finalizer must stay retired",
        )

    def test_verify72_finalizer_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Finalize zSSH FTMO runner recovery queue item",
            "zssh-ftmo-runner-recovery-queue-finalize",
            "FTMO_RUNNER_FINAL_VERIFY",
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
