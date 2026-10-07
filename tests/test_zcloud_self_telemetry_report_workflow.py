import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-self-telemetry-report-proof.yml"


class ZCloudSelfTelemetryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_exact_head_bound(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "git push",
            "systemctl restart",
            "sqlite3 ",
            "curl -X POST",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_permanent_vps_proof_runs_bounded_report(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("scripts/zcloud_self_telemetry_report.py --projects projects.json", self.text)
        self.assertIn("ZCLOUD_SELF_TELEMETRY_PROOF=", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
