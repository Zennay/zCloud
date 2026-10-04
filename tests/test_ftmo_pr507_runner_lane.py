from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-pr507-publication-integrity-full-proof.yml"


class FtmoPr507RunnerLaneTests(unittest.TestCase):
    def test_pr507_full_proof_uses_dedicated_ftmo_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertNotIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)


if __name__ == "__main__":
    unittest.main()
