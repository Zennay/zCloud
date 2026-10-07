from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = (
    WORKFLOWS / "zssh-ftmo-pr434-runner-recovery-20261002.yml",
    WORKFLOWS / "zssh-ftmo-pr437-runner-recovery-20261002.yml",
)


class ZsshFtmoPr434Pr437RecoveryRetirementTests(unittest.TestCase):
    def test_terminal_recovery_entrypoints_stay_absent(self):
        present = [str(path.relative_to(ROOT)) for path in RETIRED if path.exists()]
        self.assertEqual([], present, "terminal FTMO PR434/PR437 recovery authority returned")

    def test_retired_workflow_filenames_are_not_referenced_by_active_workflows(self):
        retired_names = {path.name for path in RETIRED}
        hits = []
        workflow_files = sorted(set(WORKFLOWS.glob("*.yml")) | set(WORKFLOWS.glob("*.yaml")))
        for path in workflow_files:
            body = path.read_text(encoding="utf-8")
            for retired_name in retired_names:
                if retired_name in body:
                    hits.append(f"{path.name}: {retired_name}")
        self.assertEqual([], hits, "active workflow still references a retired recovery entrypoint")

    def test_unique_live_recovery_markers_are_not_reintroduced(self):
        forbidden = (
            "name: Recover FTMO runner for PR434 exact head",
            "name: Recover FTMO runner for PR437 exact head",
            "group: zssh-ftmo-pr434-runner-recovery-20261002",
            "group: zssh-ftmo-pr437-runner-recovery-20261002",
            "QUEUE_ID: zssh-ftmo-pr434-runner-recovery-20261002",
            "QUEUE_ID: zssh-ftmo-pr437-runner-recovery-20261002",
        )
        hits = []
        workflow_files = sorted(set(WORKFLOWS.glob("*.yml")) | set(WORKFLOWS.glob("*.yaml")))
        for path in workflow_files:
            body = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in body:
                    hits.append(f"{path.name}: {marker}")
        self.assertEqual([], hits, "terminal runner-recovery authority was moved")


if __name__ == "__main__":
    unittest.main()
