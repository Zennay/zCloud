import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_PROVIDER_PROOF_WORKFLOWS = {
    ".github/workflows/ftmo-pr457-exact-head-proof.yml": "FTMO PR #457 closed+merged",
    ".github/workflows/ftmo-pr458-immutable-evidence-proof.yml": "FTMO PR #458 closed",
    ".github/workflows/ftmo-pr459-immutable-evidence-proof.yml": "FTMO PR #459 closed+merged",
    ".github/workflows/ftmo-pr469-exact-head-proof.yml": "FTMO PR #469 closed",
}


class RetiredFtmoProviderProofWorkflowTests(unittest.TestCase):
    def test_closed_provider_proof_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_PROVIDER_PROOF_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "create a new exact-target proof when new evidence is required "
                    "instead of reactivating closed-PR authority",
                )


if __name__ == "__main__":
    unittest.main()
