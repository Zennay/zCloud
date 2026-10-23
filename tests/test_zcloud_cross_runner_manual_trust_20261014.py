"""Trust contract for cross-runner listener proof (issue #633/#1193).

These source assertions are intentionally standalone and do not contact the VPS.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
            / "zcloud-runner-cross-lane-proof.yml")


class CrossRunnerManualTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        assert "  hosted-contract:\n" in cls.text
        assert "  prove:\n" in cls.text
        cls.hosted = cls.text.split("  hosted-contract:\n", 1)[1].split("  prove:\n", 1)[0]
        cls.live = cls.text.split("  prove:\n", 1)[1]
        cls.job_admission = cls.live.split("    runs-on:", 1)[0]
        cls.first_step = cls.live.split("      - name: Reject untrusted, rerun or stale main before VPS inspection", 1)[1].split("      - name:", 1)[0]

    def test_pull_requests_validate_on_hosted_only(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.hosted)
        self.assertNotIn("self-hosted", self.hosted)
        self.assertIn("python3 -m unittest discover -v", self.hosted)

    def test_hosted_checkout_is_immutable_and_tokenless(self):
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.hosted)
        self.assertIn("persist-credentials: false", self.hosted)
        self.assertIn("test \"$(git rev-parse HEAD)\" = \"$EXPECTED_SHA\"", self.hosted)
        self.assertRegex(self.hosted, r"uses: actions/checkout@[0-9a-f]{40}")

    def test_fork_pr_cannot_claim_trusted_hosted_proof(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.hosted)
        self.assertIn("github.repository == 'Zennay/zCloud'", self.hosted)

    def test_live_job_is_not_triggered_by_pull_request(self):
        self.assertIn("github.event_name == 'workflow_dispatch'", self.job_admission)
        self.assertNotIn("github.event_name == 'pull_request'", self.job_admission)
        self.assertIn("runs-on: [self-hosted, ftmo-research]", self.live)

    def test_live_job_requires_owner_main_and_first_attempt(self):
        for required in ("github.repository == 'Zennay/zCloud'",
                         "github.ref == 'refs/heads/main'",
                         "github.actor == 'Zennay'",
                         "github.triggering_actor == 'Zennay'",
                         "github.run_attempt == 1"):
            with self.subTest(required=required):
                self.assertIn(required, self.job_admission)

    def test_runtime_rechecks_identity_before_reading_systemd(self):
        self.assertIn('test "$GITHUB_EVENT_NAME" = "workflow_dispatch"', self.first_step)
        self.assertIn('test "$GITHUB_REPOSITORY" = "Zennay/zCloud"', self.first_step)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = "1"', self.first_step)
        self.assertIn('test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"', self.first_step)
        self.assertIn('test "$(hostname -s)" = "vps-bb300bba"', self.first_step)
        self.assertLess(self.live.index("Reject untrusted, rerun or stale main"),
                        self.live.index("Prove zCloud listener isolation"))

    def test_runtime_denies_moving_main_and_malformed_sha(self):
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.first_step)
        self.assertIn("timeout 15 git ls-remote --exit-code", self.first_step)
        self.assertIn('test "$current_main" = "$EXPECTED_SHA"', self.first_step)

    def test_never_emit_raw_recovery_journal_or_cancel_active_jobs(self):
        self.assertNotIn("journalctl ", self.live)
        self.assertNotIn("gh run cancel", self.text)
        self.assertNotIn("systemctl restart", self.live)
        self.assertNotIn("systemctl stop", self.live)

    def test_concurrency_does_not_preempt_other_jobs(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event.pull_request.number || github.ref", self.text)


if __name__ == "__main__":
    unittest.main()
