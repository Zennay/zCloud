import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FTMO_PR420_PR424_ENTRYPOINTS = {
    ".github/workflows/ftmo-pr420-runtime-recovery.yml":
        "FTMO PR #420 closed+merged; historical runtime recovery authority",
    ".github/workflows/ftmo-pr424-any-vps-fast-proof.yml":
        "FTMO PR #424 closed+merged; historical receipt writer",
    ".github/workflows/ftmo-pr421-jetta-transport-proof.yml":
        "FTMO PR #424 closed+merged despite the historical filename",
}


class RetiredFtmoPr420Pr424EntrypointTests(unittest.TestCase):
    def test_closed_ftmo_pr420_pr424_entrypoints_stay_retired(self):
        for relative_path, basis in RETIRED_FTMO_PR420_PR424_ENTRYPOINTS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a fresh narrowly-scoped proof or recovery workflow for new work",
                )


if __name__ == "__main__":
    unittest.main()
