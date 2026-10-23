"""Static fail-closed guards for Firefox session continuity install.

Hosted-only assertions. This suite never opens a browser, talks to systemd,
reads a VPS path, checks SQLite or dispatches a GitHub Actions job.
"""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/worker-firefox-session-continuity.yml")
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def jobs(source):
    verify = source.split("\n  verify:\n", 1)[1].split("\n  install-on-vps:\n", 1)[0]
    install = source.split("\n  install-on-vps:\n", 1)[1]
    return verify, install


class FirefoxSessionOwnerManualInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.verify, cls.install = jobs(cls.source)

    def test_offline_browser_recovery_checks_still_run_on_pr_and_push(self):
        for flag in ("  pull_request:", "  push:", "runs-on: ubuntu-latest",
                     "bash -n deploy/chatgpt-firefox-prestart.sh",
                     "node tests/test_worker_tabs_restore.js",
                     "test_firefox_worker_prestart.py",
                     "tests.test_firefox_session_manual_install_20261014"):
            self.assertIn(flag, self.source)

    def test_install_cannot_follow_automatic_push_or_pr(self):
        guard = self.install.split("    runs-on:", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", guard)
        self.assertNotIn("github.event_name == 'push'", guard)
        self.assertNotIn("github.event_name == 'pull_request'", guard)
        self.assertNotIn("||", guard)

    def test_default_apply_off_and_exact_sha_required(self):
        inputs = self.source.split("  workflow_dispatch:", 1)[1].split(
            "\npermissions:", 1)[0]
        self.assertRegex(inputs, r"(?s)apply:.*type: boolean.*required: true.*default: false")
        self.assertRegex(inputs, r"(?s)confirm_sha:.*type: string.*required: true")

    def test_exact_owner_repo_branch_and_input_gate(self):
        for item in (
            "github.ref == 'refs/heads/main'",
            "github.repository == 'Zennay/zCloud'",
            "github.actor == 'Zennay'",
            "inputs.apply == true",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(item=item):
                self.assertIn(item, self.install.split("    runs-on:", 1)[0])

    def test_checkout_pinned_exact_rev_no_credentials_both_jobs(self):
        for segment in (self.verify, self.install):
            self.assertIn(CHECKOUT, segment)
            self.assertIn("persist-credentials: false", segment)
            self.assertIn("clean: true", segment)
            self.assertNotIn("actions/checkout@v4", segment)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}",
                      self.verify)
        self.assertIn("ref: ${{ github.sha }}", self.install)

    def test_preflight_verifies_fresh_main_before_write(self):
        p = self.install.index("- name: Verify owner approval, current main")
        writes = self.install.index("- name: Install non-destructive recovery prestart")
        self.assertLess(p, writes)
        self.assertNotIn("install -m", self.install[:writes])
        block = self.install[p:writes]
        for exact in (
            'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
            'test "$GITHUB_ACTOR" = Zennay',
            'test "$APPROVED_APPLY" = true',
            '[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'git ls-remote --exit-code origin refs/heads/main',
            'test "$(hostname -s)" = vps-bb300bba',
            'test "$(id -un)" = ubuntu',
            "scripts/zcloud_vps_runner_guard.py --json",
        ):
            with self.subTest(exact=exact):
                self.assertIn(exact, block)

    def test_deploy_serialization_and_non_cancel_policy(self):
        self.assertIn("group: zcloud-production-deploy", self.install)
        self.assertIn("cancel-in-progress: false", self.install)

    def test_never_stop_or_restart_browser_or_clear_profile(self):
        for forbidden in ("systemctl --user stop chatgpt-firefox.service",
                          "systemctl --user restart chatgpt-firefox.service",
                          "kill -9", "pkill", "rm -rf", "profile/lock"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.install)

    def test_preserve_unsafe_loop_as_observation_not_control(self):
        self.assertIn("BROKEN_PROFILE_LOCK_RETRY_LOOP_NEEDS_OWNER_ACTION=1",
                      self.install)
        self.assertNotIn("STOPPED_BROKEN_PROFILE_LOCK_RETRY_LOOP=1",
                         self.install)
        self.assertIn('cmp deploy/chatgpt-firefox.service', self.install)

    def test_workflow_has_only_read_permissions(self):
        header = self.source.split("\npermissions:", 1)[1].split("\njobs:", 1)[0]
        self.assertIn("contents: read", header)
        self.assertNotIn("actions: write", header)
        self.assertNotIn("contents: write", header)

    def test_hosted_tests_never_access_live_vps(self):
        for forbidden in ("systemctl", "sudo ", "/home/ubuntu/",
                          "install -m", "runs-on: [self-hosted"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.verify)

    def test_prestart_and_service_checks_protect_session(self):
        for contract in (
            "! grep -Eq '^rm -[rf]+ .*sessionstore'",
            "! grep -q '^ExecStopPost=' deploy/chatgpt-firefox.service",
            'install -m 0755 deploy/chatgpt-firefox-prestart.sh',
            'install -m 0644 deploy/chatgpt-firefox.service',
            "systemctl --user daemon-reload",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.source)


if __name__ == "__main__":
    unittest.main()
