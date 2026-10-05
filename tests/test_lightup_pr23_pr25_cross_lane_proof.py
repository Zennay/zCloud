from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/lightup-pr23-pr25-cross-lane-proof.yml"


class LightUpPR23PR25CrossLaneProofTests(unittest.TestCase):
    def test_cross_lane_proof_is_exact_head_and_bounded(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            'branches:\n      - "chatgpt/lightup-pr23-pr25-cross-lane-proof-20261005"',
            text,
        )
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        for pr in (23, 24, 25):
            self.assertIn(f"resolve {pr}", text)
            self.assertIn(f'prove {pr} "$PR{pr}_SHA"', text)
        self.assertIn("python3 -m compileall -q src tests", text)
        self.assertIn("python3 -m unittest discover -s tests -v", text)
        self.assertIn("python3 -m lightup.cli scope-check 8.8.8.8", text)
        self.assertIn("python3 -m lightup.cli plan 127.0.0.1", text)
        self.assertIn('"result": "success"', text)
        self.assertIn('"23": os.environ["PR23_SHA"]', text)
        self.assertIn('"24": os.environ["PR24_SHA"]', text)
        self.assertIn('"25": os.environ["PR25_SHA"]', text)
        self.assertNotIn("git merge", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
