"""Execute the owner-only numeric-worker diagnostic gate without touching VPS."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / ".github/workflows/zcloud-worker-prompt-stop-20261013.yml").read_text(encoding="utf-8")
SHA = "0123456789abcdef0123456789abcdef01234567"


def guarded_shell():
    section = SOURCE.split("- name: Collect redacted numeric summary only", 1)[1]
    script = section.split("        run: |\n", 1)[1]
    # Stop before tests and any real private diagnostic: only the admission path executes.
    return textwrap.dedent(script.split("          python3 -m unittest -q", 1)[0])


FAKES = r"""
git() {
  case "$1" in
    rev-parse) printf '%s\n' "$SIM_LOCAL";;
    ls-remote) printf '%s\trefs/heads/main\n' "$SIM_REMOTE";;
    *) return 40;;
  esac
}
timeout() {
  test "$1" = 15 || return 41
  shift
  "$@"
}
id() { printf 'ubuntu\n'; }
hostname() { printf 'vps-bb300bba\n'; }
python3() {
  test "$1" = scripts/zcloud_vps_runner_guard.py && test "$2" = --json
}
"""


def simulate(**overrides):
    env = os.environ.copy()
    env.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REPOSITORY="Zennay/zCloud",
               GITHUB_REF="refs/heads/main", GITHUB_ACTOR="Zennay",
               GITHUB_TRIGGERING_ACTOR="Zennay", GITHUB_RUN_ATTEMPT="1",
               GITHUB_SHA=SHA, CONFIRM_SHA=SHA, APPROVE_DIAGNOSTIC="true",
               SIM_LOCAL=SHA, SIM_REMOTE=SHA)
    env.update(overrides)
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + FAKES + guarded_shell()],
                          capture_output=True, text=True, timeout=3, env=env).returncode


class WorkerDiagnosticConsentTests(unittest.TestCase):
    def test_read_only_diagnostic_requires_separate_opt_in_default_false(self):
        events = SOURCE.split("\npermissions:", 1)[0]
        self.assertRegex(events, r"approve_readonly_diagnostic:\n"
                         r"\s+description:.*\n\s+type: boolean\n"
                         r"\s+required: true\n\s+default: false")
        self.assertIn("inputs.confirm_sha == github.sha", SOURCE)
        self.assertIn("inputs.approve_readonly_diagnostic == true", SOURCE)

    def test_source_has_no_automatic_or_pr_vps_inspection(self):
        self.assertNotIn("\n  push:\n", SOURCE)
        self.assertNotIn("\n  schedule:\n", SOURCE)
        hosted = SOURCE.split("  hosted-policy-proof:\n", 1)[1].split(
            "  owner-vps-inspection:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("self-hosted", hosted)
        self.assertIn("persist-credentials: false", hosted)
        self.assertIn("tests.test_zcloud_worker_diagnostic_explicit_consent_20261014", hosted)

    def test_valid_authorized_exact_main_can_read_but_not_write(self):
        self.assertEqual(0, simulate())
        self.assertNotIn("systemctl ", SOURCE)
        self.assertNotIn("actions: write", SOURCE)
        self.assertNotIn("contents: write", SOURCE)

    def test_false_missing_or_malformed_optin_denied(self):
        for value in ("", "false", "False", "1", "TRUE"):
            with self.subTest(value=value):
                self.assertNotEqual(0, simulate(APPROVE_DIAGNOSTIC=value))

    def test_owner_and_triggering_actor_mismatch_denied(self):
        for key in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"):
            with self.subTest(key=key):
                self.assertNotEqual(0, simulate(**{key: "another-actor"}))

    def test_nonmanual_retry_and_wrong_repository_denied(self):
        for kwargs in (
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REPOSITORY": "fork/zCloud"},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(0, simulate(**kwargs))

    def test_stale_base_moving_main_or_wrong_confirm_sha_denied(self):
        for kwargs in (
            {"SIM_REMOTE": "f" * 40},
            {"SIM_REMOTE": ""},
            {"SIM_LOCAL": "f" * 40},
            {"CONFIRM_SHA": "f" * 40},
            {"CONFIRM_SHA": ""},
            {"CONFIRM_SHA": "not-a-sha"},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(0, simulate(**kwargs))

    def test_live_read_step_rechecks_main_and_optin_after_preflight(self):
        step = SOURCE.split("- name: Collect redacted numeric summary only", 1)[1]
        for check in (
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$APPROVE_DIAGNOSTIC" = "true"',
            'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
            'test "$current_main" = "$GITHUB_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            "python3 scripts/zcloud_vps_runner_guard.py --json",
        ):
            with self.subTest(check=check):
                self.assertIn(check, step)
        self.assertLess(step.index("python3 scripts/zcloud_vps_runner_guard.py --json"),
                        step.index("python3 scripts/zcloud_worker_prompt_safe_diagnostic.py"))


if __name__ == "__main__":
    unittest.main()
