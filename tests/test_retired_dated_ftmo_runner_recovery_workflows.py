import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_DATED_RECOVERY_WORKFLOWS = {
    ".github/workflows/zssh-ftmo-pr437-runner-recovery-20261002.yml":
        "FTMO PR #437 closed and recovery receipt is already durable",
    ".github/workflows/zssh-ftmo-pr434-runner-recovery-20261002.yml":
        "FTMO PR #434 closed and recovery receipt is already durable",
}


class RetiredDatedRecoveryWorkflowTests(unittest.TestCase):
    def test_historical_ftmo_runner_recovery_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_DATED_RECOVERY_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use the current bounded runner-recovery path for a new incident",
                )


if __name__ == "__main__":
    unittest.main()
