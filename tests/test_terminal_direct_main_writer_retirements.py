from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    ".github/workflows/ftmo-pr434-focused-vps-proof.yml",
    ".github/workflows/ftmo-pr521-provider-bundle-integration-proof.yml",
    ".github/workflows/ftmo-pr521-runner-recovery.yml",
    ".github/workflows/ftmo-stale-ci-zssh-queue.yml",
)


class TerminalDirectMainWriterRetirementTests(unittest.TestCase):
    def test_terminal_direct_main_writer_entrypoints_are_absent(self):
        remaining = [path for path in RETIRED if (ROOT / path).exists()]
        self.assertEqual(
            remaining,
            [],
            "terminal direct-main writer workflows must stay retired",
        )

    def test_retirement_scope_is_explicit_and_bounded(self):
        self.assertEqual(len(RETIRED), 4)
        self.assertEqual(len(set(RETIRED)), 4)
        self.assertTrue(all(path.startswith(".github/workflows/") for path in RETIRED))
        self.assertTrue(all(path.endswith((".yml", ".yaml")) for path in RETIRED))


if __name__ == "__main__":
    unittest.main(verbosity=2)
