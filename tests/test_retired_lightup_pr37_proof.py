from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "lightup-pr37-exact-head-proof.yml"


class RetiredLightUpPr37ProofTests(unittest.TestCase):
    def test_terminal_lightup_pr37_proof_stays_retired(self):
        self.assertFalse(
            RETIRED.exists(),
            "terminal LightUp PR37 exact-head workflow must stay retired",
        )

    def test_active_workflows_do_not_restore_pr37_proof_authority(self):
        markers = (
            "name: LightUp PR37 exact-head VPS proof",
            "lightup-pr37-exact-head-proof",
            "LIGHTUP_EXPECTED_SHA: 3c300c7a2882940df11cb4c4be0cde422cf91417",
            "lightup-pr37-receipt-",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        for marker in markers:
            self.assertNotIn(
                marker,
                active,
                f"retired LightUp PR37 proof marker resurrected: {marker}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
