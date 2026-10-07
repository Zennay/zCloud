from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = "finalize-ftmo-pr420-queue.yml"
TERMINAL_MARKERS = (
    "PR420_EXACT_PROOF_GREEN",
    "PR420_LIVE_RUNTIME_GREEN",
    "PR420_SQLITE_QUEUE_DONE",
)


class RetiredFtmoPr420QueueFinalizerTests(unittest.TestCase):
    def test_pr420_queue_finalizer_stays_retired(self):
        self.assertFalse((WORKFLOWS / RETIRED).exists())

    def test_pr420_finalizer_markers_are_not_active_workflow_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
