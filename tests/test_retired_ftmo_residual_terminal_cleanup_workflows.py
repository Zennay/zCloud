from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "cancel-stale-ftmo-worker5-finalizer.yml",
    "ftmo-post471-stale-pr-cleanup.yml",
    "ftmo-pr475-exact-head-proof.yml",
)

TERMINAL_MARKERS = (
    "STALE_FINALIZER_CANCEL_REQUESTED",
    "CANCELLED_STALE_PR471_RUN",
    "FTMO_PR475_ZCLOUD_PROOF_GREEN",
)


class RetiredFtmoResidualTerminalCleanupWorkflowsTests(unittest.TestCase):
    def test_residual_terminal_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_cleanup_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
