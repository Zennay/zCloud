from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-worker1-queue-finalize.yml",
    "ftmo-worker5-queue-finalize.yml",
    "ftmo-task37-pr511-zssh-recovery-queue.yml",
)

TERMINAL_MARKERS = (
    "FTMO_PR448_EXACT_PROOFS_GREEN",
    "FTMO_WORKER5_RUNTIME_GREEN",
    "zssh-ftmo-task37-pr511-runner-recovery-20261004",
)


class RetiredFtmoGen29FinalizerTask37QueueWorkflowsTests(unittest.TestCase):
    def test_historical_finalizer_and_queue_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_historical_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
