"""Source-only, deny-first contract for manually authorized Worker-v2 live proof."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / ".github/workflows/zcloud-v2-live-proof.yml").read_text(encoding="utf-8")
HOSTED = SOURCE.split("  validate:\n", 1)[1].split("  prove:\n", 1)[0]
LIVE = SOURCE.split("  prove:\n", 1)[1]
GATE = LIVE.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
PREFLIGHT = LIVE.split("Trust preflight before reading live worker/SQLite state", 1)[1].split(
    "Check live worker heartbeats on read-only SQLite and localhost", 1)[0]
READ = LIVE.split("Check live worker heartbeats on read-only SQLite and localhost", 1)[1]


class V2LiveProofExplicitApprovalTests(unittest.TestCase):
    def test_default_is_unapproved(self):
        self.assertIn("      approve_readonly_proof:\n", SOURCE)
        self.assertIn("        type: boolean\n        default: false", SOURCE)

    def test_original_exact_sha_input_still_required(self):
        self.assertIn("      confirm_sha:\n", SOURCE)
        self.assertIn("Exact current main SHA approved", SOURCE)
        self.assertIn("inputs.confirm_sha == github.sha", GATE)

    def test_job_must_have_positive_approval(self):
        self.assertIn("inputs.approve_readonly_proof == true", GATE)
        self.assertNotIn("||", GATE)

    def test_original_and_rerun_owner_preserved(self):
        for required in (
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
        ):
            with self.subTest(required=required):
                self.assertIn(required, GATE)

    def test_runtime_preflight_checks_positive_approval(self):
        self.assertIn('test "$READONLY_APPROVAL" = "true"', PREFLIGHT)
        self.assertIn('test "$GITHUB_EVENT_NAME" = "workflow_dispatch"', PREFLIGHT)
        self.assertIn('test "$CONFIRMED_SHA" = "$GITHUB_SHA"', PREFLIGHT)

    def test_recheck_approval_and_both_actors_before_read(self):
        for marker in (
            'test "$READONLY_APPROVAL" = "true"',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, READ)

    def test_exact_current_main_checked_before_and_after(self):
        self.assertIn("timeout 15 git ls-remote --exit-code origin refs/heads/main", PREFLIGHT)
        self.assertGreaterEqual(READ.count("timeout 15 git ls-remote --exit-code origin refs/heads/main"), 2)

    def test_no_raw_process_id_or_args_in_logs(self):
        self.assertNotIn("ps -eo pid=", READ)
        self.assertNotIn("ps -eo args=", READ)
        self.assertIn("ps -eo comm=", READ)
        self.assertIn('echo "BROWSER_PROCESS_COUNT=$browser_count"', READ)
        self.assertIn('[[ "$browser_count" =~ ^[0-9]{1,6}$ ]]', READ)

    def test_no_accidental_write_privileges(self):
        self.assertIn("permissions:\n  contents: read\n", SOURCE)
        self.assertNotIn("contents: write", SOURCE)
        for command in ("systemctl restart", "systemctl stop", "sudo -n", "sqlite3.connect"):
            self.assertNotIn(command, READ)

    def test_pr_only_executes_hosted(self):
        self.assertIn("if: github.event_name == 'pull_request'", HOSTED)
        self.assertIn("runs-on: ubuntu-latest", HOSTED)
        self.assertNotIn("runs-on: [self-hosted", HOSTED)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", LIVE)

    def test_exact_trusted_checkout_and_runner_guard(self):
        self.assertIn("ref: " + "$" + "{{ github.sha }}", LIVE)
        self.assertIn("persist-credentials: false", LIVE)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', PREFLIGHT)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", PREFLIGHT)

    def test_test_path_is_in_PR_trigger_and_hosted_suite(self):
        self.assertIn("      - tests/test_zcloud_v2_liveproof_explicit_approval_20261014.py", SOURCE)
        self.assertIn("tests.test_zcloud_v2_liveproof_explicit_approval_20261014", HOSTED)


if __name__ == "__main__":
    unittest.main()
