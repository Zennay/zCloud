"""Offline trusted-main admission regressions; never reads live worker state."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-worker-prompt-stop-20261013.yml").read_text(encoding="utf-8")
SHA = "0123456789abcdef0123456789abcdef01234567"


def owner_gate():
    first = SOURCE.index('          test "$GITHUB_REPOSITORY" = "Zennay/zCloud"')
    last = SOURCE.index('          test "$(id -un)" = "ubuntu"', first)
    return textwrap.dedent(SOURCE[first:last])


def admit(**overrides):
    env = os.environ.copy()
    env.update(GITHUB_REPOSITORY="Zennay/zCloud", GITHUB_REF="refs/heads/main",
               GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTOR="Zennay",
               GITHUB_TRIGGERING_ACTOR="Zennay", GITHUB_RUN_ATTEMPT="1",
               GITHUB_SHA=SHA, CONFIRM_SHA=SHA, SIM_LOCAL=SHA, SIM_REMOTE=SHA)
    env.update(overrides)
    fake = r"""
git() {
  case "$1" in
    rev-parse) printf '%s\n' "$SIM_LOCAL" ;;
    ls-remote) printf '%s\trefs/heads/main\n' "$SIM_REMOTE" ;;
    *) return 40 ;;
  esac
}
timeout() {
  test "$1" = "15" || return 50
  shift
  "$@"
}
"""
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + fake + owner_gate()],
                          env=env, capture_output=True, text=True, timeout=3).returncode


class WorkerPromptFreshMainTests(unittest.TestCase):
    def test_valid_exact_main_owner_passes_admission_only(self):
        self.assertEqual(0, admit())

    def test_untrusted_rerun_fork_and_nonmanual_denied(self):
        cases = (
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_ACTOR": "other"},
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_RUN_ATTEMPT": ""},
            {"GITHUB_REPOSITORY": "fork/zCloud"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_EVENT_NAME": "pull_request"},
        )
        for params in cases:
            with self.subTest(params=params):
                self.assertNotEqual(0, admit(**params))

    def test_moving_missing_or_ambiguous_sha_denied(self):
        cases = (
            {"SIM_REMOTE": ""}, {"SIM_REMOTE": "f" * 40},
            {"SIM_REMOTE": "bad"}, {"SIM_LOCAL": "f" * 40},
            {"CONFIRM_SHA": ""}, {"CONFIRM_SHA": "F" * 40},
            {"CONFIRM_SHA": "f" * 40}, {"CONFIRM_SHA": "bad"},
        )
        for params in cases:
            with self.subTest(params=params):
                self.assertNotEqual(0, admit(**params))

    def test_manual_job_gate_requires_owner_first_attempt_confirmed_main(self):
        live = SOURCE.split("  owner-vps-inspection:\n", 1)[1]
        gate = live.split("    runs-on:", 1)[0]
        for required in (
            "github.repository == 'Zennay/zCloud'",
            "github.event_name == 'workflow_dispatch'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(required=required):
                self.assertIn(required, gate)
        self.assertNotIn("||", gate)

    def test_fresh_runtime_preflight_precedes_sensitive_diagnostic(self):
        live = SOURCE.split("  owner-vps-inspection:\n", 1)[1]
        pre = live.split("- name: Verify owner and permanent runner", 1)[1].split(
            "- name: Collect redacted numeric summary only", 1)[0]
        for key in (
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            '[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            'test -n "$current_main"',
            'test "$current_main" = "$GITHUB_SHA"',
            "scripts/zcloud_vps_runner_guard.py --json",
        ):
            with self.subTest(key=key):
                self.assertIn(key, pre)

    def test_pr_stays_hosted_source_only(self):
        hosted = SOURCE.split("  hosted-policy-proof:\n", 1)[1].split(
            "  owner-vps-inspection:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertIn("ref: " + chr(36) + "{{ github.event.pull_request.head.sha }}", hosted)
        self.assertIn("persist-credentials: false", hosted)
        self.assertIn("tests.test_zcloud_worker_prompt_fresh_main_20261014", hosted)
        self.assertIn("tests/test_zcloud_worker_prompt_fresh_main_20261014.py", SOURCE)
        self.assertNotIn("systemctl ", SOURCE)
        self.assertNotIn("actions: write", SOURCE)
        self.assertNotIn("\n  push:\n", SOURCE)


if __name__ == "__main__":
    unittest.main()
