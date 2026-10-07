from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-post514-gate-status.yml",
    "ftmo-post514-sync-dispatch-ac2a.yml",
)

TERMINAL_MARKERS = (
    "ftmo-post514-gate-status",
    "FTMO_POST514_AC2A_SYNC_DISPATCH_REQUESTED",
)


class RetiredFtmoPost514WorkflowsTests(unittest.TestCase):
    def test_post514_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_post514_terminal_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
