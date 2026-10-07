from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-runner-self-health-proof-20261002.yml"


class FtmoDedicatedRunnerLaneTests(unittest.TestCase):
    def test_current_ftmo_runner_proof_uses_dedicated_ftmo_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertNotIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)

    def test_current_ftmo_runner_proof_remains_observation_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("sqlite3", text)
        self.assertNotIn("server.py", text)

        for mutation in (
            "systemctl start",
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "systemctl disable",
            "systemctl daemon-reload",
        ):
            self.assertNotIn(mutation, text)

        host_guard = text.index('test "$(hostname)" = "vps-bb300bba"')
        runner_guard = text.index('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"')
        first_live_observation = text.index('state="$(systemctl is-active "$SERVICE" || true)"')
        self.assertLess(host_guard, first_live_observation)
        self.assertLess(runner_guard, first_live_observation)
        self.assertIn("action=preserve-active-runner", text)


if __name__ == "__main__":
    unittest.main()
