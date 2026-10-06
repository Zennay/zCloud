import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import zcloud_deploy_summary


class DeploySummaryTests(unittest.TestCase):
    def test_green_summary_includes_sanitized_operational_evidence(self):
        summary = zcloud_deploy_summary.build_summary(
            deploy_sha="abc123",
            deploy_decision="true",
            prewrite_decision="true",
            job_status="success",
            regression_run_id="37383284780",
            regression_mode="exact",
            run_url="https://github.com/Zennay/zCloud/actions/runs/123",
            artifact_name="zcloud-production-deploy-evidence-123",
            evidence={
                "lkg_snapshot_id": "20261005T223000Z-a1b2c3d4",
                "runner_name": "zcloud-vps-1",
                "machine": "vps-bb300bba",
                "health_summary": "HEALTH_GREEN|POSTDEPLOY_GREEN",
            },
        )
        self.assertIn("Production deploy green", summary)
        self.assertIn("abc123", summary)
        self.assertIn("37383284780", summary)
        self.assertIn("zcloud-vps-1", summary)
        self.assertIn("HEALTH_GREEN\\|POSTDEPLOY_GREEN", summary)
        self.assertNotIn("HEALTH_GREEN|POSTDEPLOY_GREEN", summary)

    def test_main_move_is_reported_as_prewrite_skip(self):
        summary = zcloud_deploy_summary.build_summary(
            deploy_sha="def456",
            deploy_decision="true",
            prewrite_decision="false",
            job_status="success",
            regression_run_id="99",
            regression_mode="runtime-equivalent",
            run_url="run",
            artifact_name="artifact",
        )
        self.assertIn("Skipped because main moved before VPS writes", summary)

    def test_no_deploy_decision_is_reported_without_claiming_success(self):
        summary = zcloud_deploy_summary.build_summary(
            deploy_sha="",
            deploy_decision="false",
            prewrite_decision="",
            job_status="success",
            regression_run_id="",
            regression_mode="",
            run_url="run",
            artifact_name="artifact",
        )
        self.assertIn("Skipped before VPS mutation", summary)
        self.assertNotIn("Production deploy green", summary)


    def test_cli_writes_summary_when_evidence_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "summary.md"
            evidence = root / "evidence.json"
            evidence.write_text("{not-json", encoding="utf-8")
            argv = [
                "zcloud_deploy_summary.py",
                "--output", str(output),
                "--evidence", str(evidence),
                "--deploy-sha", "abc123",
                "--deploy-decision", "false",
                "--prewrite-decision", "",
                "--job-status", "success",
                "--regression-run-id", "",
                "--regression-mode", "",
                "--run-url", "run",
                "--artifact-name", "artifact",
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(0, zcloud_deploy_summary.main())
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("Skipped before VPS mutation", rendered)
            self.assertIn("| LKG snapshot | n/a |", rendered)



if __name__ == "__main__":
    unittest.main()
