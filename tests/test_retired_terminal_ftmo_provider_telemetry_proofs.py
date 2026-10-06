import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_TERMINAL_FTMO_PROOFS = {
    ".github/workflows/ftmo-pr408-provider-boundary-proof.yml":
        "FTMO PR #408 closed+merged",
    ".github/workflows/ftmo-pr471-future-window-proof.yml":
        "FTMO PR #471 closed+merged",
    ".github/workflows/ftmo-pr472-telemetry-proof.yml":
        "historical filename targets terminal FTMO PR #474",
}


class RetiredTerminalFtmoProofTests(unittest.TestCase):
    def test_terminal_ftmo_provider_and_telemetry_proofs_stay_retired(self):
        for relative_path, basis in RETIRED_TERMINAL_FTMO_PROOFS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a new exact-head proof workflow for future evidence",
                )


if __name__ == "__main__":
    unittest.main()
