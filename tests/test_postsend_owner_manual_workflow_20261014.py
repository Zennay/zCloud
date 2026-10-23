"""Protect owner-only, exact-main installation of the post-send Firefox source.

GitHub-hosted / local only: this suite never contacts VPS, browser, worker,
GitHub API or SQLite, and never attempts an install.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/zcloud-postsend-tab-preservation-live.yml"
)
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def job_fragment(source, name):
    match = re.search(r"(?m)^  " + re.escape(name) + r":\s*\n", source)
    if not match:
        raise AssertionError("Missing " + name + " job")
    tail = source[match.end():]
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*\n", tail)
    return tail[:next_job.start()] if next_job else tail


class PostsendOwnerManualWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.install = job_fragment(cls.source, "install")
        cls.offline = job_fragment(cls.source, "offline")

    def test_source_proof_runs_on_pull_requests(self):
        self.assertIn("  pull_request:", self.source)
        self.assertIn("  push:", self.source)
        self.assertIn("  workflow_dispatch:", self.source)
        self.assertIn("tests.test_postsend_owner_manual_workflow_20261014", self.offline)

    def test_no_automatic_push_install(self):
        gate = self.install.split("    runs-on:", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertNotIn("github.event_name == 'push'", gate)
        self.assertNotIn("||", gate)

    def test_manual_apply_defaults_to_disabled(self):
        inputs = self.source.split("  workflow_dispatch:", 1)[1].split("\npermissions:", 1)[0]
        self.assertRegex(inputs, r"(?s)apply:.*type: boolean.*default: false")
        self.assertRegex(inputs, r"(?s)confirm_sha:.*required: true.*type: string")

    def test_exact_repo_actor_branch_and_sha_gate(self):
        gate = self.install.split("    runs-on:", 1)[0]
        for required in (
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.apply == true",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(required=required):
                self.assertIn(required, gate)

    def test_hosted_proof_cannot_install(self):
        self.assertIn("runs-on: ubuntu-latest", self.offline)
        self.assertNotIn("runs-on: [self-hosted", self.offline)
        self.assertNotIn("install -m", self.offline)
        self.assertNotIn("systemctl --user", self.offline)

    def test_exact_pinned_credential_free_checkouts(self):
        for job in (self.offline, self.install):
            self.assertIn(CHECKOUT, job)
            self.assertIn("persist-credentials: false", job)
            self.assertNotIn("actions/checkout@v4", job)

    def test_checkout_yaml_cannot_have_duplicate_with_mappings(self):
        """GitHub rejects a workflow with duplicate sibling 'with:' keys before jobs start."""
        # Each job contains exactly one checkout and exactly one options mapping.
        for name, job in (("offline", self.offline), ("install", self.install)):
            with self.subTest(job=name):
                checkout = job.split("- uses: " + CHECKOUT, 1)[1].split("- name:", 1)[0]
                self.assertEqual(1, len(re.findall(r"(?m)^        with:\s*$", checkout)))
                self.assertEqual(1, len(re.findall(r"(?m)^          ref:", checkout)))
                self.assertIn("clean: true", checkout)
                self.assertIn("persist-credentials: false", checkout)

    def test_exact_source_is_checked_out_for_both_modes(self):
        offline_checkout = self.offline.split("- uses: " + CHECKOUT, 1)[1].split("- name:", 1)[0]
        manual_checkout = self.install.split("- uses: " + CHECKOUT, 1)[1].split("- name:", 1)[0]
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", offline_checkout)
        self.assertIn("ref: ${{ github.sha }}", manual_checkout)

    def test_untrusted_reruns_cannot_install(self):
        gate = self.install.split("    runs-on:", 1)[0]
        self.assertIn("github.triggering_actor == 'Zennay'", gate)
        self.assertIn('test "$GITHUB_TRIGGERING_ACTOR" = Zennay', self.install)

    def test_remote_main_recheck_is_time_bounded(self):
        self.assertIn("timeout 15s git ls-remote origin refs/heads/main", self.install)

    def test_preflight_precedes_all_file_mutations(self):
        marker = "- name: Verify manual owner approval and immutable current main"
        copying = "- name: Record running Firefox, install files, preserve session"
        self.assertIn(marker, self.install)
        self.assertLess(self.install.index(marker), self.install.index(copying))
        self.assertNotIn("install -m", self.install.split(copying, 1)[0])

    def test_runtime_identity_and_current_main_rechecked(self):
        guard = self.install.split("- name: Verify manual owner approval", 1)[1].split(
            "- name: Record running Firefox", 1
        )[0]
        for required in (
            'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
            'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
            'test "$GITHUB_REF" = refs/heads/main',
            'test "$GITHUB_ACTOR" = Zennay',
            'test "$APPLY_REQUESTED" = true',
            'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD)" = "$CONFIRM_SHA"',
            'git ls-remote origin refs/heads/main',
            'test "$current_main" = "$CONFIRM_SHA"',
            'test "$(hostname -s)" = vps-bb300bba',
            'test "$(id -un)" = ubuntu',
        ):
            with self.subTest(required=required):
                self.assertIn(required, guard)

    def test_sha_input_shape_is_validated(self):
        self.assertIn('[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]', self.install)

    def test_install_preserves_running_browser(self):
        self.assertIn("BROWSER_SERVICE_SIGNAL_SKIPPED=1", self.install)
        self.assertNotIn("systemctl --user restart", self.install)
        self.assertNotIn("systemctl --user stop", self.install)
        self.assertNotIn("pkill ", self.install)

    def test_no_privileged_workflow_token(self):
        permissions = self.source.split("\npermissions:", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("actions: write", permissions)
        self.assertNotIn("statuses: write", permissions)


if __name__ == "__main__":
    unittest.main()
