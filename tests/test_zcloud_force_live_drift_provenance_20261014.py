"""Deny automatic/unauthenticated VPS source reads and raw drift evidence in CI."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / ".github/workflows/zcloud-force-live-drift-provenance.yml"
SOURCE = PATH.read_text(encoding="utf-8")


class SourceDriftProvenancePolicyTests(unittest.TestCase):
    def test_only_pr_and_explicit_owner_manual_trigger(self):
        self.assertIn("  pull_request:\n", SOURCE)
        self.assertIn("  workflow_dispatch:\n", SOURCE)
        self.assertNotIn("\n  push:\n", SOURCE)
        self.assertNotIn("\n  schedule:\n", SOURCE)
        self.assertNotIn("\n  workflow_run:\n", SOURCE)

    def test_read_only_github_permissions_and_non_cancelling_group(self):
        self.assertIn("permissions:\n  contents: read\n", SOURCE)
        self.assertNotIn("contents: write", SOURCE)
        self.assertNotIn("actions: write", SOURCE)
        self.assertIn("cancel-in-progress: false", SOURCE)

    def test_hosted_tests_never_request_vps(self):
        hosted, live = SOURCE.split("\n  probe:\n", 1)
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("runs-on: [self-hosted", hosted)
        self.assertIn("github.event_name == 'pull_request'", hosted)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", hosted)
        self.assertIn("tests.test_zcloud_force_live_drift_provenance_20261014", hosted)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", live)

    def test_live_job_only_manual_authenticated_first_attempt(self):
        _, live = SOURCE.split("\n  probe:\n", 1)
        for token in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.approve_readonly_proof == true",
        ):
            self.assertIn(token, live)

    def test_positive_independent_explicit_approval(self):
        self.assertIn("      confirm_sha:\n", SOURCE)
        self.assertIn("      approve_readonly_proof:\n", SOURCE)
        self.assertIn('test "$APPROVED" = "true"', SOURCE)
        self.assertIn('test "$GITHUB_SHA" = "$CONFIRMED_SHA"', SOURCE)
        self.assertIn('test "$GITHUB_TRIGGERING_ACTOR" = Zennay', SOURCE)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = 1', SOURCE)

    def test_exact_current_main_both_before_and_after_read(self):
        self.assertGreaterEqual(SOURCE.count('git ls-remote --exit-code origin refs/heads/main'), 3)
        self.assertGreaterEqual(SOURCE.count('test "$(git rev-parse HEAD)" = "$CONFIRMED_SHA"'), 2)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", SOURCE)
        self.assertIn("test \"$(hostname)\" = vps-bb300bba", SOURCE)

    def test_immutable_checkout_and_no_stored_auth(self):
        self.assertEqual(SOURCE.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2)
        self.assertEqual(SOURCE.count("persist-credentials: false"), 2)
        self.assertIn("ref: " + "$" + "{{ github.sha }}", SOURCE)
        self.assertIn("ref: " + "$" + "{{ github.event.pull_request.head.sha }}", SOURCE)

    def test_no_worker_db_mutation_or_nominal_get_probe(self):
        for forbidden in (
            "api/worker-debug", "api/runner-targets", "sqlite3.connect",
            "history.db", "POST /api/", "systemctl", "sudo -n",
            "zcloud_reload_extension", "kill -", "journalctl",
        ):
            self.assertNotIn(forbidden, SOURCE)
        self.assertIn('"no_api_sqlite_or_worker_reads": True', SOURCE)

    def test_no_sensitive_detail_printed_in_report(self):
        self.assertIn('print("LIVE_SOURCE_DRIFT_AGGREGATE="', SOURCE)
        self.assertNotIn("print('FORCE_PROVENANCE_PROOF='", SOURCE)
        self.assertNotIn('"live_sha256"', SOURCE)
        self.assertNotIn('"candidate_sha256"', SOURCE)
        self.assertNotIn('"origin":', SOURCE)
        self.assertNotIn('"worker":', SOURCE)
        self.assertNotIn('"reason":', SOURCE)
        self.assertNotIn('"snapshot_id":', SOURCE)

    def test_no_live_mutation_authority_claim(self):
        self.assertIn('"mutation_authorized": False', SOURCE)
        self.assertIn('"worker_runtime_verified": False', SOURCE)
        self.assertIn('"production_verified": False', SOURCE)

    def test_proof_has_bounded_hash_and_git_reads(self):
        self.assertIn('> 2_000_000', SOURCE)
        self.assertIn('"log", "-20"', SOURCE)
        self.assertIn('timeout=10', SOURCE)
        self.assertIn('timeout=5', SOURCE)
        self.assertIn('sha256', SOURCE)

    def test_unknown_live_files_never_exceed_bounded_inventory(self):
        self.assertIn('if sum(counts.values()) != len(names):', SOURCE)
        self.assertIn('"counted_sources": len(names)', SOURCE)
        self.assertIn('if root.is_symlink() or not root.is_dir():', SOURCE)
        self.assertIn('if any(part.is_symlink()', SOURCE)


if __name__ == "__main__":
    unittest.main()
