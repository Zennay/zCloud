from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "prioritize-ftmo-post507-state-sync.yml",
    "ftmo-task37-pr511-listener-recovery.yml",
)

TERMINAL_MARKERS = (
    "FTMO_POST507_STATE_SYNC_PRIORITIZED",
    "FTMO_TASK37_SCHEDULING_GREEN",
)


class RetiredFtmoPost507Task37RunnerAuthorityTests(unittest.TestCase):
    def test_terminal_runner_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_runner_markers_are_not_active_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
