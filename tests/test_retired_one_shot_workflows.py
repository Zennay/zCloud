import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_ONE_SHOT_WORKFLOWS = {
    ".github/workflows/ftmo-pr405-hour-cache-proof.yml": "FTMO PR #405 closed+merged",
    ".github/workflows/ftmo-pr414-exact-head-proof.yml": "FTMO PR #416 closed+merged",
    ".github/workflows/ftmo-pr433-independent-proof.yml": "FTMO PR #443 closed",
    ".github/workflows/ftmo-pr434-focused-vps-proof.yml": "FTMO PR #439 closed",
}


class RetiredOneShotWorkflowTests(unittest.TestCase):
    def test_closed_ftmo_one_shot_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_ONE_SHOT_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a new narrowly-scoped workflow for new evidence instead of "
                    "reactivating historical cancellation/proof authority",
                )


if __name__ == "__main__":
    unittest.main()
