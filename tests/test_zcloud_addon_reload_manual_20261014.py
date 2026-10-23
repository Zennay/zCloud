"""Fail-closed source contract for the owner-only Firefox addon reload."""
from pathlib import Path
import re
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-emergency-addon-hot-reload.yml")


def job(source: str, name: str) -> str:
    jobs = source.split("\njobs:\n", 1)[1]
    marker = "\n  " + name + ":\n"
    return re.split(r"\n  [a-z0-9-]+:\n",
                    ("\n" + jobs).split(marker, 1)[1], maxsplit=1)[0]


class AddonReloadAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.validate = job(cls.source, "validate")
        cls.activate = job(cls.source, "activate")

    def test_never_automatically_activate_on_push_or_pr(self):
        events = self.source.split("\npermissions:", 1)[0]
        for unsafe in ("\n  push:", "\n  schedule:", "\n  workflow_run:",
                       "\n  repository_dispatch:"):
            self.assertNotIn(unsafe, events)
        self.assertIn("\n  pull_request:", events)
        self.assertIn("\n  workflow_dispatch:", events)

    def test_manual_input_requires_separate_positive_opt_in(self):
        events = self.source.split("\npermissions:", 1)[0]
        self.assertRegex(events, r"approve_addon_reload:\n"
                         r"\s+description:.*\n\s+required: true\n"
                         r"\s+type: boolean\n\s+default: false")
        self.assertIn("confirm_sha:", events)
        self.assertIn("inputs.approve_addon_reload == true", self.activate)
        self.assertIn("inputs.confirm_sha == github.sha", self.activate)

    def test_immutable_candidate_pr_is_hosted_only(self):
        self.assertIn("github.event_name == 'pull_request'", self.validate)
        self.assertIn("github.repository == 'Zennay/zCloud'", self.validate)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.validate)
        self.assertIn("runs-on: ubuntu-latest", self.validate)
        self.assertNotIn("self-hosted", self.validate)
        self.assertIn("persist-credentials: false", self.validate)
        self.assertIn("tests.test_zcloud_addon_reload_manual_20261014", self.validate)

    def test_live_job_is_never_pr_or_rerun(self):
        for check in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
        ):
            with self.subTest(check=check):
                self.assertIn(check, self.activate)
        self.assertEqual(self.source.count("runs-on: [self-hosted, zcloud, vps]"), 1)

    def test_preflight_job_has_explicit_approval_and_immutable_current_main(self):
        preflight = self.activate.split("Verify manually approved exact current main", 1)[1].split(
            "- name: Deny reload during generation", 1)[0]
        for check in (
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$APPROVE_RELOAD" = "true"',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            'test "$current_main" = "$GITHUB_SHA"',
            'python3 scripts/zcloud_vps_runner_guard.py --json',
        ):
            with self.subTest(check=check):
                self.assertIn(check, preflight)

    def test_live_reload_rechecks_all_authorization_after_prior_preflight(self):
        live = self.activate.split("Recheck exact main and idle workers", 1)[1]
        for check in (
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
            'test "$GITHUB_REF" = "refs/heads/main"',
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$APPROVE_RELOAD" = "true"',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'test "$current_main" = "$GITHUB_SHA"',
            'test "$(hostname)" = "vps-bb300bba"',
            'python3 scripts/zcloud_vps_runner_guard.py --json',
        ):
            with self.subTest(check=check):
                self.assertIn(check, live)
        self.assertLess(live.index("python3 scripts/zcloud_vps_runner_guard.py --json"),
                        live.index("node scripts/zcloud_reload_extension.mjs"))

    def test_latest_main_is_bounded_and_before_live_reads(self):
        self.assertEqual(2, self.activate.count(
            'timeout 15 git ls-remote --exit-code origin refs/heads/main'))
        self.assertIn("python3 scripts/zcloud_emergency_addon_idle_guard_20261013_w1.py",
                      self.activate)

    def test_deny_on_missing_duplicate_or_false_consent_guard(self):
        for check in (
            "inputs.approve_addon_reload == true",
            "inputs.confirm_sha == github.sha",
            "github.run_attempt == 1",
            "github.triggering_actor == 'Zennay'",
        ):
            with self.subTest(check=check):
                altered = self.activate.replace(check, "true", 1)
                self.assertNotEqual(self.activate, altered)
                self.assertNotIn(check, altered.split("    runs-on:", 1)[0])

    def test_deny_on_malformed_or_missing_live_gate(self):
        live = self.activate.split("Recheck exact main and idle workers", 1)[1]
        for check in (
            'test "$APPROVE_RELOAD" = "true"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$current_main" = "$GITHUB_SHA"',
        ):
            with self.subTest(check=check):
                self.assertNotEqual(live, live.replace(check, "false", 1))
                self.assertNotIn(check, live.replace(check, "false", 1))

    def test_no_browser_or_runtime_restart_granted(self):
        for forbidden in ("systemctl restart", "systemctl stop",
                          "pkill", "kill -9", "contents: write",
                          "actions: write", "statuses: write"):
            self.assertNotIn(forbidden, self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        self.assertIn("permissions:\n  contents: read", self.source)


if __name__ == "__main__":
    unittest.main()
