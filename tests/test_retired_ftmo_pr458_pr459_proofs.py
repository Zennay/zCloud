import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    ".github/workflows/ftmo-pr458-immutable-evidence-proof.yml",
    ".github/workflows/ftmo-pr459-immutable-evidence-proof.yml",
)


class RetiredFtmoPr458Pr459ProofTests(unittest.TestCase):
    def test_terminal_provider_proofs_stay_retired(self):
        for relative_path in RETIRED:
            with self.subTest(workflow=relative_path):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} is terminal; create a new exact-target proof "
                    "instead of restoring closed-PR write authority",
                )


if __name__ == "__main__":
    unittest.main()
