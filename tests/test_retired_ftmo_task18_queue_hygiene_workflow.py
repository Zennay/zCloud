from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-task18-queue-hygiene.yml"


class RetiredFtmoTask18QueueHygieneWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "terminal FTMO TASK-18 queue hygiene workflow must stay retired",
        )

    def test_task18_incident_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "FTMO TASK-18 queue hygiene",
            "ftmo-task18-queue-hygiene",
            "37180809155",
            "FTMO_TASK18_QUEUE_HYGIENE_GREEN",
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
