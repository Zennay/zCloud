from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-worker2-queue-snapshot.yml"


class RetiredFtmoWorker2QueueSnapshotWorkflowTests(unittest.TestCase):
    def test_retired_workflow_path_is_absent(self) -> None:
        self.assertFalse(
            RETIRED.exists(),
            "historical FTMO Worker2 queue snapshot workflow must stay retired",
        )

    def test_worker2_snapshot_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Snapshot FTMO Worker2 queue item",
            "ftmo-worker2-queue-snapshot",
            "ftmo-2e49bff2460feac8",
            "QUEUE_SNAPSHOT_RECEIPT",
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
