from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "haxlab-candidate-b-zssh-probe.yml"


class RetiredHaxlabCandidateBProbeWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "historical HaxLab Candidate-B queue probe must stay retired",
        )

    def test_candidate_b_finalizer_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Queue zSSH HaxLab Candidate-B runtime probe",
            "haxlab-candidate-b-champion-evidence-gate",
            "QUEUE_DONE_VERIFIED",
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
