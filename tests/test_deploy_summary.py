import unittest

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


if __name__ == "__main__":
    unittest.main()
