"""Offline executable trust regressions for owner-only Firefox source install.

Only extracts and evaluates admission functions in a mocked Bash environment;
never reaches the VPS, Firefox, GitHub network or a real filesystem write.
"""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-postsend-tab-preservation-live.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
SHA = "0123456789abcdef0123456789abcdef01234567"


def prewrite_guard():
    start = TEXT.index("          require_live_main() {")
    end = TEXT.index('          root="/home/ubuntu/zennay-cloud"', start)
    return textwrap.dedent(TEXT[start:end])


def admission(**overrides):
    env = os.environ.copy()
    env.update({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "Zennay/zCloud",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "Zennay",
        "GITHUB_TRIGGERING_ACTOR": "Zennay",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": SHA,
        "SIM_LOCAL": SHA,
        "SIM_REMOTE": SHA,
    })
    env.update(overrides)
    mock = """
git() {
  case "$1" in
    rev-parse) printf '%s\\n' "$SIM_LOCAL" ;;
    ls-remote) printf '%s\\trefs/heads/main\\n' "$SIM_REMOTE" ;;
    *) return 40 ;;
  esac
}
timeout() {
  test "$1" = 15 || return 50
  shift
  "$@"
}
"""
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + mock + prewrite_guard() + "\nrequire_live_main"],
        env=env, capture_output=True, text=True, timeout=4,
    )
    return result.returncode


class PostsendPrewriteTrustTests(unittest.TestCase):
    def test_exact_current_main_owner_first_attempt_is_admitted(self):
        self.assertEqual(0, admission())

    def test_replay_and_unauthorized_invocations_deny(self):
        cases = (
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_RUN_ATTEMPT": ""},
            {"GITHUB_RUN_ATTEMPT": "0"},
            {"GITHUB_ACTOR": "attacker"},
            {"GITHUB_TRIGGERING_ACTOR": "attacker"},
            {"GITHUB_REPOSITORY": "fork/zCloud"},
            {"GITHUB_REF": "refs/heads/test"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "push"},
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertNotEqual(0, admission(**case))

    def test_stale_local_or_remote_sha_deny(self):
        for case in (
            {"SIM_REMOTE": "f" * 40},
            {"SIM_REMOTE": ""},
            {"SIM_REMOTE": "bad"},
            {"SIM_LOCAL": "f" * 40},
            {"GITHUB_SHA": "f" * 39},
        ):
            with self.subTest(case=case):
                self.assertNotEqual(0, admission(**case))

    def test_only_manual_first_attempt_can_install(self):
        install = TEXT.split("  install:\n", 1)[1]
        job_gate = install.split("    runs-on:", 1)[0]
        for required in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.apply == true",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(required=required):
                self.assertIn(required, job_gate)
        self.assertNotIn("||", job_gate)

    def test_authorized_runner_guard_precedes_install(self):
        install = TEXT.split("  install:\n", 1)[1]
        admission_step = install.split("      - name: Verify manual owner approval", 1)[1].split("      - name: Record running Firefox", 1)[0]
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = "1"', admission_step)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", admission_step)
        self.assertIn('test "$current_main" = "$CONFIRM_SHA"', admission_step)

    def test_every_backup_and_write_has_local_fresh_main_guard(self):
        write_step = TEXT.split("      - name: Record running Firefox, install files, preserve session", 1)[1]
        self.assertGreaterEqual(write_step.count("require_live_main"), 9)
        for marker in (
            'install -d -m 0755 "$root/public"',
            'cp -p "$root/public/zcloud-worker.user.js"',
            'install -m 0644 public/zcloud-worker.user.js',
            'cp -p "$extension/background.js"',
            'install -m 0644 firefox-extension/background.js',
            'cp -p "$unit" "$unit.pre-postsend-$GITHUB_RUN_ID"',
            'install -D -m 0644 deploy/chatgpt-firefox.service',
            "systemctl --user daemon-reload",
        ):
            with self.subTest(marker=marker):
                pos = write_step.index(marker)
                prior = write_step.rfind("require_live_main", 0, pos)
                self.assertGreaterEqual(prior, 0)
                self.assertLess(pos-prior, 210, marker)

    def test_firefox_diagnostic_logging_never_includes_process_argv(self):
        install = TEXT.split("  install:\n", 1)[1]
        self.assertEqual(2, install.count("ps -eo pid=,ppid=,comm="))
        self.assertNotIn("ps -eo pid=,ppid=,args=", install)
        self.assertIn("head -n 20", install)
        self.assertNotIn("journalctl ", install)
        self.assertNotIn("pgrep -af", install)

    def test_no_browser_restart_or_auto_install(self):
        self.assertIn("runs-on: ubuntu-latest", TEXT)
        self.assertIn("BROWSER_SERVICE_SIGNAL_SKIPPED=1", TEXT)
        for forbidden in ("systemctl --user restart ", "systemctl --user stop ", "pkill ", "gh run cancel"):
            self.assertNotIn(forbidden, TEXT)
        self.assertIn("persist-credentials: false", TEXT)


if __name__ == "__main__":
    unittest.main()
