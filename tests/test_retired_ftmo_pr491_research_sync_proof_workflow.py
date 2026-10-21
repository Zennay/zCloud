from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-pr491-research-sync-proof.yml"


class RetiredFtmoPr491ResearchSyncProofWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "closed/superseded FTMO PR491 proof workflow must stay retired",
        )

    def test_terminal_pr491_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "FTMO PR491 research-state sync publication proof",
            "ftmo-pr491-research-sync-proof",
            "runtime-evidence/ftmo-pr491-research-sync-proof.json",
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
