from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    WORKFLOWS / "lightup-pr41-exact-head-proof.yml",
    WORKFLOWS / "lightup-pr42-exact-head-proof.yml",
)


class RetiredLightUpPr41Pr42ProofTests(unittest.TestCase):
    def test_terminal_lightup_pr41_pr42_proofs_stay_retired(self):
        for path in RETIRED:
            self.assertFalse(path.exists(), f"terminal workflow must stay retired: {path.name}")

    def test_active_workflows_do_not_restore_pr41_pr42_authority(self):
        markers = (
            "name: LightUp PR41 exact-head VPS proof",
            "name: LightUp PR42 exact-head VPS proof",
            "lightup-pr41-exact-head-proof",
            "lightup-pr42-exact-head-proof",
            "LIGHTUP_EXPECTED_SHA: 7668943e0f8dd719d95e6da4f30f621cc8adf642",
            "LIGHTUP_EXPECTED_SHA: 8afa27a7d1846bdc08ffb221e087360d2378c27d",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        for marker in markers:
            self.assertNotIn(marker, active, f"retired LightUp proof marker resurrected: {marker}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
