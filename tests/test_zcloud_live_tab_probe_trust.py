"""Contract tests for the live Firefox tab probe's actual GitHub trust boundary.

The only permissible PR execution is a hosted, read-only source regression.
The self-hosted Firefox RDP probe must be limited to the canonical repository's
main push or an explicitly owner-initiated manual dispatch on main.
"""
import ast
import os
import textwrap
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / ".github/workflows/zcloud-live-tab-version-probe.yml").read_text(encoding="utf-8")


def _job(name):
    marker = "  " + name + ":\n"
    start = SOURCE.index(marker, SOURCE.index("\njobs:\n"))
    remaining = SOURCE[start + len(marker):]
    next_job = remaining.find("\n  hosted-policy-regression:")
    next_inspect = remaining.find("\n  inspect:")
    cutoffs = [n for n in (next_job, next_inspect) if n >= 0]
    return SOURCE[start:start + len(marker)] + (remaining[:min(cutoffs)] if cutoffs else remaining)


def _step_event_guard():
    start = SOURCE.index('          case "${GITHUB_EVENT_NAME:-}:${GITHUB_ACTOR:-}" in')
    end = SOURCE.index("          esac", start) + len("          esac")
    return "\n".join(line[10:] for line in SOURCE[start:end].splitlines())


def _event_guard_result(event, actor):
    env = dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_ACTOR=actor)
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + _step_event_guard()],
                          env=env, capture_output=True, text=True, timeout=3)


class LiveTabProbeTrustTests(unittest.TestCase):
    def test_only_canonical_main_can_be_self_hosted(self):
        inspect = _job("inspect")
        self.assertIn("github.repository == 'Zennay/zCloud'", inspect)
        self.assertIn("github.ref == 'refs/heads/main'", inspect)
        self.assertIn("github.event_name == 'push'", inspect)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.actor == 'Zennay'", inspect)

    def test_pr_checks_are_hosted_only(self):
        hosted = _job("hosted-policy-regression")
        inspect = _job("inspect")
        self.assertIn("if: github.event_name == 'pull_request'", hosted)
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("self-hosted", hosted)
        self.assertIn("runs-on: [self-hosted, ftmo-research]", inspect)
        self.assertNotIn("pull_request", inspect.split("runs-on:", 1)[0].replace(
            "github.event_name == 'push'", "").replace("github.event_name == 'workflow_dispatch'", ""))

    def test_hosted_job_uses_immutable_checkout_without_write_credentials(self):
        hosted = _job("hosted-policy-regression")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", hosted)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", hosted)
        self.assertIn("persist-credentials: false", hosted)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', hosted)

    def test_step_level_runner_identity(self):
        inspect = _job("inspect")
        for guard in (
            'test "${GITHUB_REPOSITORY:-}" = "Zennay/zCloud"',
            'test "${GITHUB_REF:-}" = "refs/heads/main"',
            'test "${RUNNER_OS:-}" = "Linux"',
            'test "$(id -un)" = "ubuntu"',
            'test "$(hostname)" = "vps-bb300bba"',
        ):
            self.assertIn(guard, inspect)
        self.assertLess(inspect.index('test "${GITHUB_REPOSITORY:-}"'),
                        inspect.index("python3 - <<'PY'"))

    def test_manual_owner_allowed(self):
        self.assertEqual(0, _event_guard_result("workflow_dispatch", "Zennay").returncode)

    def test_manual_nonowner_denied(self):
        for actor in ("", "someone-else", "zennay", "github-actions[bot]"):
            with self.subTest(actor=actor):
                self.assertNotEqual(0, _event_guard_result("workflow_dispatch", actor).returncode)

    def test_pr_and_other_events_denied_at_runtime(self):
        for event in ("pull_request", "pull_request_target", "workflow_run", ""):
            with self.subTest(event=event):
                self.assertNotEqual(0, _event_guard_result(event, "Zennay").returncode)

    def test_main_push_may_read_probe_only(self):
        self.assertEqual(0, _event_guard_result("push", "github-actions[bot]").returncode)
        inspect = _job("inspect")
        self.assertIn("READ_ONLY_PROBE_NO_TAB_OR_BROWSER_CHANGES=1", inspect)
        self.assertNotIn("actions/checkout@", inspect)
        for forbidden in ("systemctl ", "sqlite3.connect(", "os.kill(", "subprocess.Popen("):
            self.assertNotIn(forbidden, inspect)

    def test_concurrency_and_permissions(self):
        header = SOURCE.split("\njobs:\n", 1)[0]
        self.assertIn("contents: read", header)
        self.assertNotIn("actions: write", header)
        self.assertNotIn("issues: write", header)
        self.assertIn("cancel-in-progress: false", header)
        self.assertIn("workflow_dispatch:", header)
        self.assertNotIn("pull_request_target:", header)


    def test_embedded_rdp_probe_is_valid_python(self):
        embedded = SOURCE.split("python3 - <<'PY'", 1)[1].split("\n          PY", 1)[0]
        ast.parse(textwrap.dedent(embedded), filename="zcloud-live-tab-version-probe.yml")

    def test_every_selected_tab_is_observed_before_claiming_version_health(self):
        inspect = _job("inspect")
        self.assertIn("sampled=chat[:20]", inspect)
        self.assertIn("for tab in sampled:", inspect)
        self.assertIn('send(sock,{"to":tab_actor,"type":"getTarget"})', inspect)
        self.assertNotIn('chat[0]["actor"]', inspect)
        self.assertIn("len(chat)<=20 and len(versions)==len(chat)", inspect)
        self.assertIn("RDP_SAMPLE_COMPLETE=", inspect)
        self.assertIn("RDP_IS_TARGET_SCRIPT_LIVE=", inspect)



if __name__ == "__main__":
    unittest.main()
