from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    WORKFLOWS / "ftmo-task37-proof-clean-278251fd.yml",
    WORKFLOWS / "ftmo-stale-run-cleanup-20261004.yml",
)


class RetiredFtmoTask37AndStaleRunCleanupWorkflowTests(unittest.TestCase):
    def test_retired_workflow_paths_are_absent(self) -> None:
        for path in RETIRED:
            self.assertFalse(
                path.exists(),
                f"terminal historical FTMO workflow must stay retired: {path.name}",
            )

    def test_terminal_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "FTMO TASK37 focused proof 278251fd clean",
            "ftmo-task37-proof-clean-278251fd",
            "FTMO stale exact-head run cleanup 20261004",
            "FTMO_STALE_RUNS_CANCELLED",
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
