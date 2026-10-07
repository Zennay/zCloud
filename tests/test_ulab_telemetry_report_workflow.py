from pathlib import Path
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "zcloud-ulab-telemetry-report-proof.yml"
)


class ULabTelemetryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_head_and_least_privilege(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_live_proof_is_read_only_and_permanent_vps_bound(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("scripts/zcloud_ulab_telemetry_report.py", self.text)
        for forbidden in ("curl -X POST", "curl -X PATCH", "curl -X PUT", "curl -X DELETE", "sqlite3 ", "systemctl restart", "sudo "):
            self.assertNotIn(forbidden, self.text)

    def test_hosted_contract_runs_before_live_metadata_read(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("needs: validate", self.text)
        self.assertIn("tests.test_ulab_telemetry_report", self.text)
        self.assertIn("tests.test_ulab_telemetry_report_workflow", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
