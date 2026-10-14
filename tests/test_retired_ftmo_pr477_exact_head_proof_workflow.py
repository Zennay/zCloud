from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-pr477-exact-head-proof.yml"


class RetiredFtmoPr477ExactHeadProofWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "closed FTMO PR477 merge/proof workflow must stay retired",
        )

    def test_terminal_pr477_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "FTMO PR477 exact-head telemetry proof",
            "ftmo-pr477-exact-head-proof",
            "FTMO_PR477_ZCLOUD_MERGE_GREEN",
            "runtime-evidence/ftmo-pr477-zcloud-proof.json",
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
