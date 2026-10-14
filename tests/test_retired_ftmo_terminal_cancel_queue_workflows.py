from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-cancel-one-verified-stale-run.yml",
    "ftmo-cancel-stale-ci-zssh-queue.yml",
    "ftmo-cancel-superseded-ci-zssh-queue.yml",
)

TERMINAL_MARKERS = (
    "FTMO_WORKER5_STALE_CANCEL_COUNT",
    "zssh-ftmo-cancel-stale-pr430-432-ci-20261002",
    "zssh-ftmo-cancel-superseded-ci-v2-20261002",
)


class RetiredFtmoTerminalCancelQueueWorkflowsTests(unittest.TestCase):
    def test_terminal_cancel_queue_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_cancel_queue_markers_are_not_active_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
