"""Guards the automatic queue-cancellation quarantine (no live GitHub mutation)."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-hosted-emergency-queue-sweep.yml").read_text(encoding="utf-8")

class EmergencyQueueQuarantineTests(unittest.TestCase):
    def test_never_scheduled_or_automatically_pushed(self):
        self.assertNotRegex(SOURCE, r"(?m)^  (schedule|push|workflow_run|repository_dispatch):")
        self.assertIn("  workflow_dispatch:", SOURCE)
        self.assertIn("  pull_request:", SOURCE)

    def test_permissions_are_read_only(self):
        self.assertIn("permissions:\n  contents: read\n  actions: read", SOURCE)
        for forbidden in ("actions: write", "statuses: write", "contents: write", "pull-requests: write"):
            self.assertNotIn(forbidden, SOURCE)

    def test_no_cancel_or_status_write_api_routes(self):
        for forbidden in ('method="POST"', 'method="DELETE"', 'method="PATCH"', "/cancel", "/statuses/", "git push", "systemctl", "sudo ", "rm -rf"):
            self.assertNotIn(forbidden, SOURCE)
        self.assertIn('method="GET"', SOURCE)
        self.assertIn('"mutation_performed": False', SOURCE)

    def test_no_production_vps_or_other_workers_access(self):
        self.assertNotIn("self-hosted", SOURCE.split("jobs:", 1)[0])
        self.assertIn("runs-on: ubuntu-latest", SOURCE)
        self.assertNotIn("runs-on: [self-hosted", SOURCE)
        self.assertNotIn("/api/dynamic-workers", SOURCE)

    def test_pr_source_checks_cannot_inspect_live_queue(self):
        before, after = SOURCE.split("\n  observe:", 1)
        self.assertIn("  source-proof:", before)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", before)
        self.assertIn("python3 -m unittest", before)
        self.assertNotIn("GH_TOKEN:", before)
        self.assertIn("github.event_name == 'workflow_dispatch'", after)
        self.assertIn("github.actor == 'Zennay'", after)
        self.assertIn("github.triggering_actor == 'Zennay'", after)
        self.assertIn("github.run_attempt == 1", after)
        self.assertIn("inputs.confirm_sha == github.sha", after)
        self.assertIn("current_main", after) if False else None
        self.assertIn("actual_main", after)

    def test_checkout_is_exact_and_without_credentials(self):
        self.assertEqual(2, SOURCE.count("uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"))
        self.assertEqual(2, SOURCE.count("persist-credentials: false"))
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", SOURCE)
        self.assertIn("ref: ${{ github.sha }}", SOURCE)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', SOURCE)

    def test_queue_evidence_is_bounded_and_not_claimed_complete(self):
        self.assertIn('"sample_complete": True', SOURCE)
        self.assertIn('summary["sample_complete"] = False', SOURCE)
        self.assertIn("runs[:20]", SOURCE)
        self.assertIn('summary["runs_with_no_job_records"] += 1', SOURCE)
        self.assertIn('summary["indeterminate_job_rows"] += 1', SOURCE)
        self.assertIn('"stale_queued_jobs"', SOURCE)
        self.assertNotIn('print(json.dumps(run', SOURCE)
        self.assertNotIn('print(json.dumps(jobs', SOURCE)

    def test_non_canceling_concurrency(self):
        self.assertIn("cancel-in-progress: false", SOURCE)

if __name__ == "__main__":
    unittest.main()
