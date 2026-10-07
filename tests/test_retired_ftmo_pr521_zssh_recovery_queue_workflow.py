from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-runner-recovery-zssh-queue.yml"


class RetiredFtmoPr521ZsshRecoveryQueueWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "terminal FTMO PR521 zSSH recovery enqueue must stay retired",
        )

    def test_pr521_recovery_enqueue_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Enqueue FTMO runner recovery on zSSH",
            "zssh-ftmo-pr521-runner-recovery-20261004",
            "FTMO_RUNNER_RECOVERY_ENQUEUE",
            "artifacts/ops/ftmo-runner-recovery-zssh-queue.json",
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
