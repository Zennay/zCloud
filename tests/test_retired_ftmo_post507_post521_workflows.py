from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "recover-ftmo-listener-post507-sync.yml",
    "ftmo-post521-preservation-gate.yml",
)

TERMINAL_MARKERS = (
    "FTMO_POST507_STATE_SYNC_SCHEDULING_GREEN",
    "post-pr521-preservation-proof",
)


class RetiredFtmoPost507Post521WorkflowsTests(unittest.TestCase):
    def test_historical_entrypoints_stay_retired(self):
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
