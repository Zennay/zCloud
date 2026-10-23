"""Fail-closed proof for the owner-only worker tab-guard live install.

These tests are hosted/static; never use a VPS, browser, worker queue or token.
"""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-worker-tab-guard.yml")
PINNED_CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def job(source: str, name: str) -> str:
    match = re.search(r"(?m)^  " + re.escape(name) + r":\s*$", source)
    if match is None:
        raise AssertionError("Missing job: " + name)
    rest = source[match.end():]
    later = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", rest)
    return rest[:later.start()] if later else rest


class WorkerTabManualInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.unit = job(cls.source, "unit")
        cls.install = job(cls.source, "install")

    def test_hosted_regressions_remain_on_pull_request_and_push(self):
        self.assertIn("  pull_request:", self.source)
        self.assertIn("  push:", self.source)
        self.assertIn("runs-on: ubuntu-latest", self.unit)
        self.assertIn("tests.test_worker_tab_guard_manual_install_20261014", self.unit)

    def test_manual_default_is_read_only(self):
        header = self.source.split("  workflow_dispatch:", 1)[1].split("\npermissions:", 1)[0]
        self.assertRegex(header, r"(?s)apply:.*type: boolean.*required: true.*default: false")
        self.assertRegex(header, r"(?s)confirm_sha:.*type: string.*required: true")

    def test_install_cannot_follow_pr_or_push(self):
        gate = self.install.split("    runs-on:", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertNotIn("github.event_name == 'push'", gate)
        self.assertNotIn("github.event_name == 'pull_request'", gate)
        self.assertNotIn("||", gate)

    def test_full_identity_and_sha_bound_gate(self):
        gate = self.install.split("    runs-on:", 1)[0]
        for field in (
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.apply == true",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(field=field):
                self.assertIn(field, gate)

    def test_pinned_checkout_and_no_token_persistence(self):
        for section in (self.unit, self.install):
            self.assertIn(PINNED_CHECKOUT, section)
            self.assertIn("persist-credentials: false", section)
            self.assertNotIn("actions/checkout@v4", section)

    def test_authorization_before_any_live_install(self):
        proof = self.install.index("- name: Verify exact immutable main")
        install = self.install.index("- name: Install verified worker browser")
        self.assertLess(proof, install)
        self.assertNotIn("install -m", self.install[:install])

    def test_runtime_fail_closed_on_remote_main_drift(self):
        guard = self.install.split("- name: Verify exact immutable main", 1)[1].split(
            "- name: Install verified worker browser", 1)[0]
        for text in (
            'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
            'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
            'test "$GITHUB_REF" = refs/heads/main',
            'test "$GITHUB_ACTOR" = Zennay',
            'test "$APPLY" = true',
            'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD)" = "$CONFIRM_SHA"',
            'git ls-remote origin refs/heads/main',
            'test "$current_main" = "$CONFIRM_SHA"',
            'test "$(hostname -s)" = vps-bb300bba',
            'test "$(id -un)" = ubuntu',
            "python3 scripts/zcloud_vps_runner_guard.py --json",
        ):
            with self.subTest(text=text):
                self.assertIn(text, guard)

    def test_sha_is_strict_lowercase_40_hex(self):
        self.assertIn('[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]', self.install)

    def test_no_hosted_path_to_service_or_extension_mutation(self):
        self.assertNotIn("systemctl", self.unit)
        self.assertNotIn("install -m", self.unit)
        self.assertNotIn("zcloud_reload_extension.mjs", self.unit)

    def test_only_read_contents_permission(self):
        permissions = self.source.split("\npermissions:", 1)[1].split("\njobs:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertNotIn("actions: write", permissions)
        self.assertNotIn("statuses: write", permissions)

    def test_browser_reload_retains_idle_gate_and_no_kill(self):
        self.assertIn("Worker allocation changed during idle window", self.install)
        self.assertIn("scripts/zcloud_reload_extension.mjs", self.install)
        self.assertNotIn("systemctl restart", self.install)
        self.assertNotIn("pkill", self.install)


if __name__ == "__main__":
    unittest.main()
