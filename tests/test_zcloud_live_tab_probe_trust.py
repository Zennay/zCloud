"""Contract tests for the live Firefox tab probe's actual GitHub trust boundary.

The only permissible PR execution is a hosted, read-only source regression.
The self-hosted Firefox RDP probe must be limited to the canonical repository's
an explicitly owner-initiated first-attempt manual dispatch on exact main.
"""
import ast
import os
import re
import tempfile
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
    start = SOURCE.index('          case "${GITHUB_EVENT_NAME:-}:${GITHUB_ACTOR:-}:${GITHUB_TRIGGERING_ACTOR:-}:${GITHUB_RUN_ATTEMPT:-}" in')
    end = SOURCE.index("          esac", start) + len("          esac")
    return "\n".join(line[10:] for line in SOURCE[start:end].splitlines())


def _event_guard_result(event, actor, triggering_actor="Zennay", attempt="1"):
    env = dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_ACTOR=actor,
               GITHUB_TRIGGERING_ACTOR=triggering_actor,
               GITHUB_RUN_ATTEMPT=attempt)
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + _step_event_guard()],
                          env=env, capture_output=True, text=True, timeout=3)


class LiveTabProbeTrustTests(unittest.TestCase):
    def test_only_canonical_main_can_be_self_hosted(self):
        inspect = _job("inspect")
        self.assertIn("github.repository == 'Zennay/zCloud'", inspect)
        self.assertIn("github.ref == 'refs/heads/main'", inspect)
        self.assertIn("github.event_name == 'workflow_dispatch'", inspect)
        self.assertIn("github.actor == 'Zennay'", inspect)
        self.assertIn("github.triggering_actor == 'Zennay'", inspect)
        self.assertIn("github.run_attempt == 1", inspect)
        self.assertIn("inputs.confirm_sha == github.sha", inspect)
        self.assertNotIn("github.event_name == 'push'", inspect)

    def test_pr_checks_are_hosted_only(self):
        hosted = _job("hosted-policy-regression")
        inspect = _job("inspect")
        self.assertIn("if: github.event_name == 'pull_request' || github.event_name == 'push'", hosted)
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("self-hosted", hosted)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", inspect)
        self.assertNotIn("pull_request", inspect.split("runs-on:", 1)[0].replace(
            "github.event_name == 'push'", "").replace("github.event_name == 'workflow_dispatch'", ""))

    def test_hosted_job_uses_immutable_checkout_without_write_credentials(self):
        hosted = _job("hosted-policy-regression")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", hosted)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", hosted)
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
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", inspect)
        self.assertLess(
            inspect.index("python3 scripts/zcloud_vps_runner_guard.py --json"),
            inspect.index("python3 - <<'PY'"),
        )
        self.assertLess(inspect.index('test "${GITHUB_REPOSITORY:-}"'),
                        inspect.index("python3 - <<'PY'"))

    def test_manual_owner_allowed(self):
        self.assertEqual(0, _event_guard_result("workflow_dispatch", "Zennay").returncode)

    def test_manual_nonowner_denied(self):
        for actor in ("", "someone-else", "zennay", "github-actions[bot]"):
            with self.subTest(actor=actor):
                self.assertNotEqual(0, _event_guard_result("workflow_dispatch", actor).returncode)

    def test_manual_reruns_and_untrusted_triggering_actor_denied(self):
        self.assertNotEqual(0, _event_guard_result(
            "workflow_dispatch", "Zennay", triggering_actor="attacker").returncode)
        for attempt in ("", "0", "2", "01"):
            with self.subTest(attempt=attempt):
                self.assertNotEqual(0, _event_guard_result(
                    "workflow_dispatch", "Zennay", attempt=attempt).returncode)

    def test_pr_and_other_events_denied_at_runtime(self):
        for event in ("pull_request", "pull_request_target", "workflow_run", "push", ""):
            with self.subTest(event=event):
                self.assertNotEqual(0, _event_guard_result(event, "Zennay").returncode)

    def test_main_push_can_only_run_hosted_source_tests(self):
        self.assertNotEqual(0, _event_guard_result("push", "github-actions[bot]").returncode)
        self.assertNotEqual(0, _event_guard_result("push", "Zennay").returncode)
        inspect = _job("inspect")
        self.assertIn("READ_ONLY_PROBE_NO_TAB_OR_BROWSER_CHANGES=1", inspect)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", inspect)
        for forbidden in ("systemctl ", "sqlite3.connect(", "os.kill(", "subprocess.Popen("):
            self.assertNotIn(forbidden, inspect)

    def test_exact_main_sha_and_origin_verification_precede_debugger_access(self):
        inspect = _job("inspect")
        for required in (
            '[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            'test -n "$current_main"',
            'test "$current_main" = "$GITHUB_SHA"',
            'test "${GITHUB_RUN_ATTEMPT:-}" = "1"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, inspect)
        self.assertLess(inspect.index('test "$current_main" = "$GITHUB_SHA"'),
                        inspect.index("python3 - <<'PY'"))

    def test_concurrency_and_permissions(self):
        header = SOURCE.split("\njobs:\n", 1)[0]
        self.assertIn("contents: read", header)
        self.assertNotIn("actions: write", header)
        self.assertNotIn("issues: write", header)
        self.assertIn("cancel-in-progress: false", header)
        self.assertIn("workflow_dispatch:", header)
        self.assertIn("confirm_sha:", header)
        self.assertIn("required: true", header)
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



    def _run_version_preflight(self, candidate):
        embedded = SOURCE.split("python3 - <<'PY'", 1)[1].split("\n          PY", 1)[0]
        start = embedded.index('          source=Path("public/zcloud-worker.user.js")')
        endmarker = 'print("RDP_EXPECTED_SCRIPT_VERSION="+expected_version)'
        end = embedded.index(endmarker, start) + len(endmarker)
        script = "import re\nfrom pathlib import Path\n" + textwrap.dedent(embedded[start:end])
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "public" / "zcloud-worker.user.js"
            file.parent.mkdir(parents=True)
            file.write_text(candidate, encoding="utf-8")
            return subprocess.run(
                ["python3", "-c", script], cwd=temp,
                text=True, capture_output=True, timeout=5,
            )

    def test_runtime_version_comes_from_actual_v2_source(self):
        script_source = (ROOT / "public/zcloud-worker.user.js").read_text(encoding="utf-8")
        match = re.search(r"(?m)^//\s*@version\s+(\d+\.\d+\.\d+)\s*$", script_source)
        self.assertIsNotNone(match)
        outcome = self._run_version_preflight(script_source)
        self.assertEqual(0, outcome.returncode, outcome.stderr)
        self.assertIn("RDP_EXPECTED_SCRIPT_VERSION=" + match.group(1), outcome.stdout)
        self.assertIn("set(versions)=={expected_version}", SOURCE)
        self.assertIn('re.fullmatch(r"\d+\.\d+\.\d+",version)', SOURCE)
        self.assertNotIn('set(versions)=={"1.3.18"}', SOURCE)

    def test_mismatched_source_metadata_denies_health(self):
        script_source = (ROOT / "public/zcloud-worker.user.js").read_text(encoding="utf-8")
        corrupted = re.sub(
            r"(?m)^(//\s*@version\s+)\d+\.\d+\.\d+",
            r"\g<1>9.9.9", script_source, count=1,
        )
        self.assertNotEqual(script_source, corrupted)
        outcome = self._run_version_preflight(corrupted)
        self.assertNotEqual(0, outcome.returncode)
        self.assertIn("source version/DOM readiness mismatch", outcome.stderr)

    def test_new_version_does_not_need_workflow_rewrite(self):
        script_source = (ROOT / "public/zcloud-worker.user.js").read_text(encoding="utf-8")
        original = re.search(r"(?m)^//\s*@version\s+(\d+\.\d+\.\d+)\s*$", script_source)
        self.assertIsNotNone(original)
        revised = script_source.replace(original.group(1), "7.8.9")
        self.assertNotEqual(revised, script_source)
        outcome = self._run_version_preflight(revised)
        self.assertEqual(0, outcome.returncode, outcome.stderr)
        self.assertIn("RDP_EXPECTED_SCRIPT_VERSION=7.8.9", outcome.stdout)

    def test_self_hosted_checkout_is_exact_immutable_main(self):
        inspect = _job("inspect")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", inspect)
        self.assertIn("ref: ${{ github.sha }}", inspect)
        self.assertIn("persist-credentials: false", inspect)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', inspect)
        self.assertLess(inspect.index('test "$(git rev-parse HEAD)"'), inspect.index("python3 - <<'PY'"))


if __name__ == "__main__":
    unittest.main()
