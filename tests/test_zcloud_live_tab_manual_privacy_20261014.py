"""Offline contract for manually approved, privacy-preserving Firefox tab proof."""
import ast
from pathlib import Path
import re
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / ".github/workflows/zcloud-live-tab-version-probe.yml").read_text(encoding="utf-8")


def job(name):
    marker = "\n  " + name + ":\n"
    start = SOURCE.index(marker, SOURCE.index("\njobs:\n"))
    rest = SOURCE[start + len(marker):]
    next_job = re.search(r"(?m)^  [a-zA-Z_-]+:\s*$", rest)
    return SOURCE[start:] if next_job is None else SOURCE[start:start + len(marker) + next_job.start()]


class LiveTabPrivacyTests(unittest.TestCase):
    def test_manual_positive_approval(self):
        self.assertIn("      approve_readonly_probe:\n", SOURCE)
        self.assertIn("        type: boolean\n        default: false", SOURCE)
        self.assertIn("inputs.approve_readonly_probe == true", job("inspect"))
        self.assertIn('test "$APPROVE_READONLY" = "true"', job("inspect"))

    def test_exact_head_confirmation(self):
        self.assertIn("inputs.confirm_sha == github.sha", job("inspect"))
        self.assertIn('test "$CONFIRM_SHA" = "$GITHUB_SHA"', job("inspect"))
        self.assertIn('[[ "$CONFIRM_SHA" =~ ^[0-9a-f]{40}$ ]]', job("inspect"))

    def test_owner_only_first_attempt(self):
        source = job("inspect")
        for item in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
        ):
            with self.subTest(item=item):
                self.assertIn(item, source)

    def test_hosted_pr_never_touches_vps(self):
        hosted = job("hosted-policy-regression")
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("runs-on: [self-hosted", hosted)
        self.assertIn("tests.test_zcloud_live_tab_manual_privacy_20261014", hosted)

    def test_trigger_paths_cover_new_contract(self):
        self.assertEqual(SOURCE.count("      - tests/test_zcloud_live_tab_manual_privacy_20261014.py"), 2)

    def test_no_raw_debug_identity_logging(self):
        for forbidden in (
            'print("FIREFOX_MAIN_PID="', 'print("RDP_ROOT_FIELD_NAMES="',
            'print("RDP_TAB_FIELD_NAMES="', 'print("RDP_SCRIPT_VERSIONS="',
            'print("RDP_RESPONSE_KIND="',
        ):
            with self.subTest(item=forbidden):
                self.assertNotIn(forbidden, SOURCE)

    def test_only_aggregate_versions_and_redacted_response(self):
        self.assertIn('print("RDP_MATCHED_EXPECTED_COUNT="', SOURCE)
        self.assertIn('print("RDP_NONMATCHING_OR_UNKNOWN_COUNT="', SOURCE)
        self.assertIn('print("RDP_RESPONSE_DETAIL_REDACTED=1")', SOURCE)
        self.assertIn('print("RDP_IS_TARGET_SCRIPT_LIVE="', SOURCE)

    def test_ambiguous_or_invalid_debugger_denied(self):
        self.assertIn("from scripts.zcloud_live_tab_rdp_port_20261016 import detect_debugger_port", SOURCE)
        self.assertIn('status,port=detect_debugger_port(proc.stdout.splitlines())', SOURCE)
        self.assertIn('if status!="unique" or port is None:', SOURCE)
        self.assertNotIn("if len(matches)!=1", SOURCE)
        self.assertIn("RDP_SAFE_PROBE_UNAVAILABLE=ambiguous_debugger", SOURCE)
        self.assertIn("if not 1 <= port <= 65535:", SOURCE)
        self.assertIn('raise ValueError("invalid local debugger port")', SOURCE)

    def test_frame_size_and_sample_bounded(self):
        self.assertIn("if remaining>2_000_000:", SOURCE)
        self.assertIn("sampled=chat[:20]", SOURCE)
        self.assertIn("len(chat)<=20 and len(versions)==len(chat)", SOURCE)

    def test_vps_and_exact_main_checked_before_probe(self):
        source = job("inspect")
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', source)
        self.assertIn("GITHUB_RUN_ATTEMPT", source)
        self.assertIn('test "$current_main" = "$GITHUB_SHA"', source)
        self.assertIn("persist-credentials: false", source)

    def test_no_mutating_vps_commands(self):
        for forbidden in ("systemctl restart", "systemctl stop", "sudo -n", "sqlite3.connect("):
            self.assertNotIn(forbidden, job("inspect"))
        self.assertIn("permissions:\n  contents: read\n", SOURCE)

    def test_embedded_source_compiles(self):
        embedded = SOURCE.split("python3 - <<'PY'", 1)[1].split("\n          PY", 1)[0]
        ast.parse(textwrap.dedent(embedded))


if __name__ == "__main__":
    unittest.main()
