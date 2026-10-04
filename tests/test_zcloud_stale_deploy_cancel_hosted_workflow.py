from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HostedStaleDeployCleanupWorkflowTests(unittest.TestCase):
    def test_cleanup_is_hosted_and_reacts_to_green_regression(self):
        text = (
            ROOT / ".github/workflows/zcloud-stale-deploy-cancel-hosted.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('workflows: ["zCloud regression smoke"]', text)
        self.assertIn("types: [completed]", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("actions: write", text)

    def test_cleanup_preserves_current_main_and_rechecks_before_cancel(self):
        text = (
            ROOT / ".github/workflows/zcloud-stale-deploy-cancel-hosted.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('commits/main" --jq .sha', text)
        self.assertIn(
            "actions/workflows/zcloud-vps-deploy.yml/runs?per_page=100",
            text,
        )
        self.assertIn("actions/runs/$run_id", text)
        self.assertGreaterEqual(text.count('if [[ "$fresh_sha" == "$current_main" ]]'), 1)
        self.assertIn("ZCLOUD_STALE_DEPLOY_KEEP_CURRENT", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_CANCEL_REQUESTED", text)
        self.assertIn('actions/runs/$run_id/cancel', text)

    def test_cleanup_has_no_hardcoded_guarded_deploy_run_ids(self):
        text = (
            ROOT / ".github/workflows/zcloud-stale-deploy-cancel-hosted.yml"
        ).read_text(encoding="utf-8")

        hardcoded_run_ids = re.findall(r"\b37\d{9}\b", text)
        self.assertEqual([], hardcoded_run_ids)


if __name__ == "__main__":
    unittest.main()
