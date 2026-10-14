from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = (
    "ftmo-pr471-future-window-proof.yml",
    "ftmo-pr481-deploy-publication-proof.yml",
)

TERMINAL_MARKERS = (
    "FTMO_PR471_TARGETED_BOUNDARIES_GREEN",
    "FTMO_PR481_DEPLOY_PUBLICATION_PROOF",
    "FTMO_PR481_MERGE_SHA",
)


class RetiredFtmoPr471Pr481ProofAuthorityTests(unittest.TestCase):
    def test_terminal_proof_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_terminal_proof_and_merge_markers_are_not_active_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
        )
        for marker in TERMINAL_MARKERS:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
