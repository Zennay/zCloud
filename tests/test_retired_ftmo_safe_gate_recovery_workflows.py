from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    WORKFLOWS / "ftmo-safe-gate-zssh-recovery-20261002.yml",
    WORKFLOWS / "ftmo-exact-safe-gate-runtime-recovery.yml",
)


class RetiredFtmoSafeGateRecoveryWorkflowTests(unittest.TestCase):
    def test_retired_workflow_paths_are_absent(self) -> None:
        for path in RETIRED:
            self.assertFalse(
                path.exists(),
                f"stale FTMO safe-gate recovery workflow must stay retired: {path.name}",
            )

    def test_stale_safe_gate_mutation_markers_are_absent_from_active_workflows(self) -> None:
        markers = (
            "Enqueue FTMO safe-gate recovery on zSSH",
            "Recover exact FTMO safe-gate runtime",
            "FTMO_SAFE_GATE_RUNTIME_RECONCILED",
            "zssh-ftmo-exact-safe-gate-3b512f56",
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
