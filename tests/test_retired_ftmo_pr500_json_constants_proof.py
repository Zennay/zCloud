from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = "ftmo-pr500-json-constants-proof.yml"


class RetiredFtmoPr500JsonConstantsProofTests(unittest.TestCase):
    def test_terminal_pr500_proof_path_is_absent(self) -> None:
        self.assertFalse(
            (WORKFLOWS / RETIRED).exists(),
            f"{RETIRED} must stay retired",
        )

    def test_terminal_pr500_proof_identity_is_not_reintroduced(self) -> None:
        markers = (
            "FTMO PR500 JSON constants exact-head proof",
            "FTMO_PR500_JSON_CONSTANTS_PROOF=success",
            "hardening/autonomous-json-constants-20261004",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for pattern in ("*.yml", "*.yaml")
            for path in sorted(WORKFLOWS.glob(pattern))
        )
        for marker in markers:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
