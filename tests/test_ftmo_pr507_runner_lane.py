from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-runner-self-health-proof-20261002.yml"


class FtmoDedicatedRunnerLaneTests(unittest.TestCase):
    def test_current_ftmo_runner_proof_uses_dedicated_ftmo_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertNotIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)


if __name__ == "__main__":
    unittest.main()
