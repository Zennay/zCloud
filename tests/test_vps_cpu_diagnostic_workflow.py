import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/vps-cpu-diagnostic.yml")


class VpsCpuDiagnosticWorkflowTests(unittest.TestCase):
    def test_self_hosted_execution_is_trusted_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository", text
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_process_diagnostics_do_not_emit_full_argv(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("names only; no argv", text)
        self.assertIn("stat,comm --sort=-%cpu", text)
        self.assertIn("stat,comm \\", text)
        self.assertNotIn("stat,cmd", text)
        self.assertNotIn("ps -ef", text)
        self.assertNotIn("args --sort", text)
        self.assertNotIn("command --sort", text)

    def test_diagnostic_remains_bounded_and_observational(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("ZCLOUD_VPS_CPU_DIAGNOSTIC_GREEN=1", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("systemctl stop", text)
        self.assertNotIn("systemctl start", text)


if __name__ == "__main__":
    unittest.main()
