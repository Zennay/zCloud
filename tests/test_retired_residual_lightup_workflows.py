from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED_WORKFLOWS = (
    "lightup-ftmo-listener-recovery.yml",
    "lightup-pr20-pr21-cross-lane-proof.yml",
    "lightup-pr23-pr25-cross-lane-proof.yml",
    "lightup-pr40-zcloud-runner-recovery.yml",
    "lightup-pr40-generic-listener-recovery.yml",
)
RETIRED_TESTS = (
    "test_lightup_ftmo_listener_recovery.py",
    "test_lightup_cross_lane_proof_workflow.py",
    "test_lightup_pr23_pr25_cross_lane_proof.py",
)


class RetiredResidualLightUpWorkflowTests(unittest.TestCase):
    def test_historical_workflow_and_contract_paths_are_absent(self) -> None:
        for name in RETIRED_WORKFLOWS:
            self.assertFalse((WORKFLOWS / name).exists(), f"{name} must stay retired")
        for name in RETIRED_TESTS:
            self.assertFalse((ROOT / "tests" / name).exists(), f"{name} must stay retired")

    def test_terminal_lightup_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "LightUp PR20 PR21 cross-lane exact-head proof",
            "LightUp PR23 PR24 PR25 cross-lane exact-head proof",
            "Recover FTMO listener for LightUp ST3 proof",
            "Recover zCloud runner for LightUp PR40 proof",
            "LightUp PR40 generic zCloud listener recovery",
            "LIGHTUP_RUN_ID: \"37343030852\"",
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
