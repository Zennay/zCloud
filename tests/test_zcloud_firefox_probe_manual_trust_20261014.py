"""Source-only safety regression for the permanent-VPS Firefox RDP probe."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/".github/workflows/zcloud-firefox-worker-recovery-probe-20261013.yml").read_text(encoding="utf-8")


class FirefoxProbeManualTrustTests(unittest.TestCase):
    def test_auto_triggers_have_only_hosted_ci(self):
        self.assertIn("  pull_request:\n",SOURCE)
        self.assertIn("  push:\n",SOURCE)
        self.assertNotIn("  workflow_run:\n",SOURCE)
        self.assertNotIn("  schedule:\n",SOURCE)
        hosted,live=SOURCE.split("\n  inspect:\n",1)
        self.assertIn("runs-on: ubuntu-latest",hosted)
        self.assertNotIn("runs-on: [self-hosted",hosted)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]",live)

    def test_manual_dispatch_requires_independent_confirmation(self):
        self.assertIn("      confirm_sha:\n",SOURCE)
        self.assertIn("      approve_readonly_probe:\n",SOURCE)
        self.assertIn("type: boolean\n        default: false",SOURCE)
        self.assertIn("inputs.approve_readonly_probe == true",SOURCE)
        self.assertIn("inputs.confirm_sha == github.sha",SOURCE)

    def test_manual_job_event_and_owner_provenance(self):
        live=SOURCE.split("\n  inspect:\n",1)[1]
        required=("github.event_name == 'workflow_dispatch'",
                  "github.repository == 'Zennay/zCloud'",
                  "github.actor == 'Zennay'",
                  "github.triggering_actor == 'Zennay'",
                  "github.run_attempt == 1",
                  "github.ref == 'refs/heads/main'")
        for marker in required:
            self.assertIn(marker,live)

    def test_manual_step_rechecks_actual_actor_and_sha(self):
        for marker in ("test \"$GITHUB_EVENT_NAME\" = workflow_dispatch",
                       'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
                       'test "$GITHUB_RUN_ATTEMPT" = 1',
                       'test "$APPROVED" = true',
                       'test "$GITHUB_SHA" = "$CONFIRMED_SHA"'):
            self.assertIn(marker,SOURCE)

    def test_current_main_revalidated_across_read_steps(self):
        self.assertGreaterEqual(SOURCE.count("git ls-remote --exit-code origin refs/heads/main"),3)
        self.assertGreaterEqual(SOURCE.count('test "$(git rev-parse HEAD)" = "$CONFIRMED_SHA"'),2)

    def test_permanent_runner_proof_retained(self):
        self.assertIn('test "$(id -un)" = ubuntu',SOURCE)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json",SOURCE)
        self.assertIn("test \"$(hostname)\" = \"vps-bb300bba\"",SOURCE)

    def test_hosted_candidate_sha_bound(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository",SOURCE)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',SOURCE)
        self.assertIn("ref: "+"$"+"{{ github.event.pull_request.head.sha || github.sha }}",SOURCE)

    def test_immutable_checkout_and_no_stored_credentials(self):
        self.assertEqual(SOURCE.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"),2)
        self.assertEqual(SOURCE.count("persist-credentials: false"),2)
        self.assertIn("permissions:\n  contents: read\n",SOURCE)
        self.assertNotIn("contents: write",SOURCE)

    def test_no_live_mutation(self):
        for forbidden in ("systemctl restart","systemctl stop","sudo -n",
                          "api/dynamic-workers","curl -X POST",
                          "sqlite3.connect", "rm -rf", "browser.tabs.remove"):
            self.assertNotIn(forbidden,SOURCE)

    def test_backend_nominal_get_not_used(self):
        self.assertIn("RUNNER_TARGETS_HTTP_SKIPPED=backend_get_may_write",SOURCE)
        self.assertNotIn("urlopen(",SOURCE)
        self.assertNotIn("api/worker-debug",SOURCE)

    def test_rdp_header_limited_to_ten_bytes(self):
        self.assertIn("for _ in range(10):",SOURCE)
        self.assertIn('raise ValueError("oversize RDP frame header")',SOURCE)
        self.assertIn("if not prefix:",SOURCE)

    def test_rdp_payload_size_bounded(self):
        self.assertIn("n<1 or n>1_000_000",SOURCE)
        self.assertIn("sock.recv(min(65536,n-len(data)))",SOURCE)

    def test_rdp_eof_fails_closed(self):
        self.assertIn('if not part:raise EOFError("incomplete RDP response")',SOURCE)
        self.assertIn('data.decode("utf-8","strict")',SOURCE)

    def test_no_debugger_port_list_or_browser_commands_logged(self):
        self.assertNotIn('print("FIREFOX_DEBUGGER_PORTS=',SOURCE)
        self.assertIn('print("FIREFOX_DEBUGGER_PROCESS_COUNT=',SOURCE)
        self.assertNotIn('print("FIREFOX_COMMAND_LINES=',SOURCE)
        self.assertIn("READ_ONLY_NO_PROCESS_OR_TAB_MUTATION=1",SOURCE)


if __name__=="__main__":
    unittest.main()
