from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RetiredFtmoPr475RecoveryWorkflowTests(unittest.TestCase):
    def test_terminal_pr475_recovery_entrypoints_stay_retired(self):
        retired = [
            WORKFLOWS / "ftmo-pr475-dedicated-runner-recovery.yml",
            WORKFLOWS / "ftmo-pr475-haxlab-runner-recovery.yml",
            WORKFLOWS / "ftmo-pr475-stale-telemetry-ci-cleanup.yml",
        ]
        for path in retired:
            self.assertFalse(
                path.exists(),
                f"retired FTMO PR475 recovery workflow must not return: {path.name}",
            )

    def test_terminal_stale_run_ids_are_not_reintroduced(self):
        terminal_run_ids = {"37162440132", "37162452829"}
        offenders = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            matched = sorted(run_id for run_id in terminal_run_ids if run_id in text)
            if matched:
                offenders[path.name] = matched
        self.assertEqual(
            offenders,
            {},
            f"terminal FTMO telemetry run IDs must stay out of active workflows: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
