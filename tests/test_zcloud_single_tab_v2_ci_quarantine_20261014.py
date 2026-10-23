"""Keep one-tab Worker-v2 CI hosted-only; never implicitly deploy Firefox from main.

A separate owner-controlled Worker-v2 release path owns production changes.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/zcloud-single-tab-v2.yml"
)


class OneTabV2SourceOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_no_push_or_pr_can_own_live_vps_install(self):
        self.assertIn("  pull_request:\n    branches: [main]", self.text)
        self.assertIn("  push:\n    branches: [main]", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertEqual(self.text.count("runs-on:"), 1)
        for prohibited in (
            "  install:", "self-hosted", "actions: write", "contents: write",
            "pull_request_target:", "workflow_run:", "workflow_dispatch:",
            "systemctl ", "bash scripts/zcloud_retire_legacy_worker_services.sh",
            "node scripts/zcloud_reload_extension.mjs", "install -m ",
            "mkdir -p \"$backup\"", "sudo ", "GH_TOKEN:",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, self.text)

    def test_pr_and_main_source_paths_are_identical(self):
        trigger = self.text.split("\npermissions:", 1)[0]
        pr = trigger.split("  pull_request:\n", 1)[1].split("  push:\n", 1)[0]
        push = trigger.split("  push:\n", 1)[1]
        self.assertEqual(pr, push)
        for name in (
            "public/zcloud-worker.user.js",
            "public/zcloud-worker-v2-engine.js",
            "firefox-extension/background.js",
            "firefox-extension/worker-controller-v2.js",
            "firefox-extension/manifest.json",
            "tests/test_single_tab_v2_crash_recovery.js",
            "tests/test_single_tab_v2_auto_heal.js",
            "tests/test_v2_auto_open_missing_worker_ids_20261013_w4.js",
            "tests/test_v2_dispatch_recovery_20261013_w3.js",
            "tests/test_zcloud_single_tab_v2_ci_quarantine_20261014.py",
        ):
            with self.subTest(path=name):
                self.assertIn("      - " + name, pr)

    def test_checkout_exact_sha_before_any_v2_code_and_no_credentials(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertIn(
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
            self.text,
        )
        self.assertEqual(self.text.count("node-version: 24"), 1)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("clean: true", self.text)
        self.assertIn("fetch-depth: 1", self.text)
        self.assertIn('test "$EXPECTED_REPOSITORY" = "Zennay/zCloud"', self.text)
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository",
                      self.text)
        self.assertLess(
            self.text.index("Prove exact canonical source before executing one-tab code"),
            self.text.index("Validate v2 one-tab ownership and High-thinking gate"),
        )
        identity = self.text.split("Prove exact canonical source", 1)[1].split(
            "Validate v2 one-tab ownership", 1
        )[0]
        self.assertNotIn("node tests/", identity)
        self.assertNotIn("python3 ", identity)

    def test_real_v2_regressions_not_lost_by_live_quarantine(self):
        commands = (
            "node --check firefox-extension/background.js",
            "node --check firefox-extension/worker-controller-v2.js",
            "node --check public/zcloud-worker-v2-engine.js",
            "node --check public/zcloud-worker.user.js",
            "node tests/test_single_tab_v2_crash_recovery.js",
            "node tests/test_single_tab_v2_auto_heal.js",
            "node tests/test_v2_auto_open_missing_worker_ids_20261013_w4.js",
            "node tests/test_single_tab_worker_v2.js",
            "node tests/test_single_tab_v2_thinking_high.js",
            "node tests/test_v2_workmode_high_controls.js",
            "node tests/test_single_tab_v2_cycle.js",
            "node tests/test_v2_dispatch_recovery_20261013_w3.js",
            "node tests/test_worker_tab_identity_guard.js",
            "node tests/test_worker_tab_reuse.js",
            "node tests/test_firefox_recovery_contract.js",
            "tests.test_violentmonkey_runner",
            "tests.test_zcloud_single_tab_v2_ci_quarantine_20261014",
            "ONE_TAB_PER_WORKER_V2_TESTS_GREEN=1",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.text)


if __name__ == "__main__":
    unittest.main()
