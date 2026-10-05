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
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)
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
        self.assertIn("ZCLOUD_STALE_DEPLOY_KEEP_IN_PROGRESS", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_NO_CURRENT_GUARD", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_GREEN_TRIGGER_GUARDLESS", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_GREEN_TRIGGER_AUTHORIZES_SUPERSEDED_CANCEL", text)
        self.assertIn('trigger_sha="$(jq -r', text)
        self.assertIn('trigger_branch="$(jq -r', text)
        self.assertIn('trigger_conclusion="$(jq -r', text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_MAIN_MOVED_ABORT", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_CURRENT_GUARD_GONE_ABORT", text)
        self.assertIn("current_guard_ids=()", text)
        self.assertIn("current_in_progress_ids=()", text)
        self.assertIn("current_waiting_ids=()", text)
        self.assertIn('keeper_current_waiting=""', text)
        self.assertIn("authoritative_guard_active=false", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_KEEP_CURRENT_NEWEST", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_DUPLICATE_CURRENT_CANDIDATE", text)
        self.assertIn("ZCLOUD_STALE_DEPLOY_CANCEL_DUPLICATE_CURRENT", text)
        self.assertIn("queued|pending|waiting|requested", text)
        self.assertEqual(2, text.count("IFS=$\'\\t\' read -r run_id status head_sha"))
        self.assertNotIn("queued|in_progress|pending|waiting|requested)\n                ;;\n              *)\n                echo", text)
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
