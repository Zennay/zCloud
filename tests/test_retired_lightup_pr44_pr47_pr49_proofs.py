from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = tuple(
    WORKFLOWS / name
    for name in (
        "lightup-pr44-exact-head-proof.yml",
        "lightup-pr47-exact-head-proof.yml",
        "lightup-pr49-exact-head-proof.yml",
    )
)


class RetiredLightUpPr44Pr47Pr49ProofTests(unittest.TestCase):
    def test_terminal_lightup_proofs_stay_retired(self):
        for path in RETIRED:
            self.assertFalse(path.exists(), f"terminal workflow must stay retired: {path.name}")

    def test_active_workflows_do_not_restore_retired_proof_authority(self):
        markers = (
            "name: LightUp PR44 exact-head VPS proof",
            "name: LightUp PR47 exact-head VPS proof",
            "name: LightUp PR49 exact-head VPS proof",
            "LIGHTUP_EXPECTED_SHA: 2a678f33ecc71bed09f298f7136a48514ccb15fc",
            "LIGHTUP_EXPECTED_SHA: c1d9e3c15ca3ffe7b2583e4296ea192d4f482f6d",
            "LIGHTUP_EXPECTED_SHA: 70b49cebd9d7d43dc5883d190c4afcacebbfd0ff",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        for marker in markers:
            self.assertNotIn(marker, active, f"retired LightUp proof marker resurrected: {marker}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
