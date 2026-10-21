import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FTMO_JSON_PROOF_WORKFLOWS = {
    ".github/workflows/ftmo-pr500-autonomous-json-proof.yml": "FTMO PR #500 closed",
    ".github/workflows/ftmo-pr510-permission-hardening-proof.yml": "FTMO PR #510 closed",
}


class RetiredFtmoJsonProofWorkflowTests(unittest.TestCase):
    def test_closed_ftmo_json_proof_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_FTMO_JSON_PROOF_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a fresh narrowly-scoped workflow for new evidence instead of "
                    "reactivating historical merge or proof authority",
                )


if __name__ == "__main__":
    unittest.main()
