from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-runner-self-health-proof-20261002.yml"


class FtmoDedicatedRunnerLaneTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_current_ftmo_runner_proof_uses_dedicated_ftmo_runner(self):
        self.assertIn("runs-on: [self-hosted, ftmo-research]", self.text)
        self.assertNotIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)

    def test_self_health_evidence_is_bounded_and_does_not_dump_raw_diagnostics(self):
        self.assertIn("FTMO_RUNNER_PROCESS_COUNT=", self.text)
        self.assertIn("FTMO_KERNEL_OOM_FRESH_COUNT=", self.text)
        self.assertIn("FTMO_RUNNER_DIAG_EVENT_COUNT=", self.text)
        self.assertIn('test "$listener" = "1"', self.text)
        self.assertNotIn("FTMO_RUNNER_PROCESSES_BEGIN", self.text)
        self.assertNotIn("FTMO_KERNEL_OOM_FRESH_BEGIN", self.text)
        self.assertNotIn("FTMO_RUNNER_DIAG_FRESH_BEGIN", self.text)
        self.assertNotIn("DIAG_FILE=", self.text)
        self.assertNotIn("ps -o pid=,ppid=,lstart=,etime=,cmd=", self.text)
        self.assertNotIn('echo "$cmdline"', self.text)

    def test_self_health_proof_stays_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("systemctl restart", self.text)
        self.assertNotIn("systemctl reset-failed", self.text)
        self.assertNotIn("systemctl start", self.text)
        self.assertNotIn("gh api --method", self.text)


if __name__ == "__main__":
    unittest.main()
