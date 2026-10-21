import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FTMO_AUTHORITY_WORKFLOWS = {
    ".github/workflows/ftmo-pr475-exact-head-proof.yml": "FTMO PR #475 closed",
    ".github/workflows/ftmo-pr477-exact-head-proof.yml": "FTMO PR #477 closed+merged",
    ".github/workflows/ftmo-pr481-deploy-publication-proof.yml": "FTMO PR #481 closed",
    ".github/workflows/ftmo-pr491-research-sync-proof.yml": "FTMO PR #491 closed",
}


class RetiredFtmoAuthorityWorkflowTests(unittest.TestCase):
    def test_closed_target_authority_workflows_stay_retired(self):
        for relative_path, basis in RETIRED_FTMO_AUTHORITY_WORKFLOWS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "new proof/merge authority must use a fresh exact-target workflow "
                    "rather than reactivating closed-PR automation",
                )


if __name__ == "__main__":
    unittest.main()
