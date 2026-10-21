import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_TERMINAL_FTMO_ENTRYPOINTS = {
    ".github/workflows/ftmo-pr407-stale-ci-zssh-queue.yml":
        "FTMO PR #407 closed+merged and its exact stale-CI queue ID has no other references",
}


class RetiredTerminalFtmoEntrypointTests(unittest.TestCase):
    def test_terminal_pr407_entrypoint_stays_retired(self):
        for relative_path, basis in RETIRED_TERMINAL_FTMO_ENTRYPOINTS.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a fresh narrowly-scoped workflow for new work",
                )


if __name__ == "__main__":
    unittest.main()
