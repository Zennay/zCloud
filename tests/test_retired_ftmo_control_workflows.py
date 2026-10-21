import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FTMO_CONTROL_WORKFLOWS = {
    ".github/workflows/ftmo-pr500-json-constants-proof.yml": "FTMO PR #500 closed",
    ".github/workflows/ftmo-pr501-autonomous-json-proof.yml": "FTMO PR #501 closed",
    ".github/workflows/ftmo-pr512-queue-hygiene.yml": "FTMO PR #512 closed",
    ".github/workflows/ftmo-pr514-runtime-private-proof.yml": "FTMO PR #514 closed+merged",
}


class RetiredFtmoControlWorkflowTests(unittest.TestCase):
    def test_closed_target_control_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_FTMO_CONTROL_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "new merge, cancellation, runner-recovery or proof authority must "
                    "use a fresh exact-target workflow",
                )


if __name__ == "__main__":
    unittest.main()
