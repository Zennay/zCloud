import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    "lightup-pr40-exact-head-proof.yml",
    "lightup-pr40-generic-listener-recovery.yml",
    "lightup-pr40-zcloud-runner-recovery.yml",
)
TERMINAL_FINGERPRINTS = (
    "29e720902e062b215560665677328c59cdc9ec9d",
    "37343030852",
    "37341085861",
    "LightUp PR40 exact-head VPS proof",
    "LIGHTUP_PR40_GENERIC_RECOVERY",
    "LIGHTUP_PR40_RECOVERY_GUARD_GREEN",
)


class RetiredLightUpPr40RecoveryWorkflowsTests(unittest.TestCase):
    def test_terminal_pr40_entrypoints_stay_retired(self):
        for name in RETIRED:
            self.assertFalse(
                (WORKFLOWS / name).exists(),
                f"terminal LightUp PR40 workflow resurrected: {name}",
            )

    def test_active_workflows_do_not_restore_terminal_authority(self):
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.y*ml"))
            if path.is_file()
        )
        for fingerprint in TERMINAL_FINGERPRINTS:
            self.assertNotIn(
                fingerprint,
                active,
                f"terminal LightUp PR40 authority fingerprint resurrected: {fingerprint}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
