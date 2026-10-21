from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-pr512-queue-hygiene.yml",
    "ftmo-pr514-runtime-private-proof.yml",
)

TERMINAL_MARKERS = (
    "FTMO_PR512_QUEUE_GREEN",
    "FTMO_PR514_FOCUSED_PROOF_GREEN",
)


class RetiredFtmoPr512Pr514WorkflowsTests(unittest.TestCase):
    def test_terminal_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_markers_are_not_reintroduced_in_active_workflows(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
