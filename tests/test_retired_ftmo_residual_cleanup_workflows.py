from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "cancel-stale-ftmo-worker5-finalizer.yml",
    "ftmo-post471-stale-pr-cleanup.yml",
)

UNIQUE_TERMINAL_MARKERS = (
    "CANCELLED_STALE_PR471_RUN",
,)


class RetiredFtmoResidualCleanupWorkflowsTests(unittest.TestCase):
    def test_terminal_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_unique_terminal_marker_is_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in UNIQUE_TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
