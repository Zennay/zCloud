from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-task90-merge-tree-proof.yml"


class RetiredFtmoTask90MergeTreeProofWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "terminal FTMO TASK-90 merge-tree proof must stay retired",
        )

    def test_task90_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "FTMO TASK-90 merge tree-equivalence proof",
            "ftmo-task90-merge-tree-proof",
            "reliability/task-90-merge-tree-proof-b579124",
            "runtime-evidence/ftmo-task90-merge-tree-proof.json",
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
