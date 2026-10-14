"""Source + executable admission regressions for manual watchdog hotfix.

PRs may validate offline, but any root-owned runtime installation must be a
separate owner-authorized manual dispatch on exact canonical main.
"""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-no-browser-churn-hotfix.yml"
SOURCE = WORKFLOW.read_text(encoding="utf-8")


def _release_guard():
    anchor = '          test "$GITHUB_EVENT_NAME" = "workflow_dispatch"'
    start = SOURCE.index(anchor)
    end = SOURCE.index('          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', start)
    return "\n".join(line[10:] for line in SOURCE[start:end].splitlines())


def _try_owner_admission(**overrides):
    env = dict(os.environ)
    env.update(
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REPOSITORY="Zennay/zCloud",
        GITHUB_REF="refs/heads/main",
        GITHUB_ACTOR="Zennay",
        GITHUB_SHA="0123456789abcdef0123456789abcdef01234567",
        CONFIRM_SHA="0123456789abcdef0123456789abcdef01234567",
        APPLY="true",
    )
    env.update(overrides)
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + _release_guard()],
        env=env, capture_output=True, text=True, timeout=5,
    ).returncode


class NoBrowserChurnManualGateTests(unittest.TestCase):
    def test_valid_exact_owner_intent_passes_admission_only(self):
        self.assertEqual(0, _try_owner_admission())

    def test_untrusted_trigger_denied(self):
        for name in ("push", "pull_request", "workflow_run", "pull_request_target", ""):
            with self.subTest(event=name):
                self.assertNotEqual(0, _try_owner_admission(GITHUB_EVENT_NAME=name))

    def test_missing_false_or_unconfirmed_apply_denied(self):
        for apply in ("", "false", "TRUE", "0"):
            with self.subTest(apply=apply):
                self.assertNotEqual(0, _try_owner_admission(APPLY=apply))
        for sha in ("", "other", "0123456789abcdef0123456789abcdef01234566"):
            with self.subTest(sha=sha):
                self.assertNotEqual(0, _try_owner_admission(CONFIRM_SHA=sha))

    def test_wrong_actor_repo_or_ref_denied(self):
        for key, value in (
            ("GITHUB_ACTOR", "github-actions[bot]"),
            ("GITHUB_ACTOR", "other"),
            ("GITHUB_REPOSITORY", "fork/zCloud"),
            ("GITHUB_REF", "refs/heads/feature"),
        ):
            with self.subTest(key=key, value=value):
                self.assertNotEqual(0, _try_owner_admission(**{key: value}))

    def test_no_automatic_main_push_install_entrypoint(self):
        self.assertNotIn("\n  push:\n", SOURCE)
        self.assertNotIn("pull_request_target:", SOURCE)
        self.assertIn("workflow_dispatch:", SOURCE)
        self.assertIn("pull_request:", SOURCE)
        self.assertIn("default: false", SOURCE)
        self.assertIn("cancel-in-progress: false", SOURCE)

    def test_install_requires_exact_manual_owner_boolean_and_sha(self):
        self.assertIn("github.event_name == 'workflow_dispatch'", SOURCE)
        self.assertIn("github.actor == 'Zennay'", SOURCE)
        self.assertIn("github.repository == 'Zennay/zCloud'", SOURCE)
        self.assertIn("github.ref == 'refs/heads/main'", SOURCE)
        self.assertIn("inputs.apply == true", SOURCE)
        self.assertIn("inputs.confirm_sha == github.sha", SOURCE)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", SOURCE)

    def test_both_checkouts_immutable_uncredentialed(self):
        self.assertEqual(2, SOURCE.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"))
        self.assertEqual(2, SOURCE.count("persist-credentials: false"))
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', SOURCE)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', SOURCE)
        self.assertIn("runs-on: ubuntu-latest", SOURCE)
        self.assertNotIn("actions: write", SOURCE)

    def test_guard_precedes_any_sudo_or_runtime_install(self):
        self.assertLess(SOURCE.index("test \"$CONFIRM_SHA\" = \"$GITHUB_SHA\""), SOURCE.index("sudo -n install"))
        self.assertLess(SOURCE.index("scripts/zcloud_vps_runner_guard.py --json"), SOURCE.index("sudo -n install"))
        self.assertNotIn("systemctl restart", SOURCE.split("- name: Install only", 1)[-1])
        self.assertNotIn("kill -", SOURCE.split("- name: Install only", 1)[-1])


if __name__ == "__main__":
    unittest.main()
