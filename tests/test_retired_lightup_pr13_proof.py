from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "lightup-pr13-exact-head-proof.yml"


class RetiredLightUpPr13ProofTests(unittest.TestCase):
    def test_terminal_lightup_pr13_proof_stays_retired(self):
        self.assertFalse(
            RETIRED.exists(),
            "terminal LightUp PR13 exact-head workflow must stay retired",
        )

    def test_active_workflows_do_not_restore_pr13_proof_authority(self):
        markers = (
            "name: LightUp PR13 exact-head VPS proof",
            "lightup-pr13-exact-head-vps-proof",
            'LIGHTUP_PR: "13"',
            "lightup-pr13-scope.json",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        for marker in markers:
            self.assertNotIn(
                marker,
                active,
                f"retired LightUp PR13 proof marker resurrected: {marker}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
