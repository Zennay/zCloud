from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-hosted-resilience-simulation.yml"


class HostedResilienceSimulationWorkflowTests(unittest.TestCase):
    def test_gate_is_hosted_read_only_and_exact_revision(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$actual" = "$EXPECTED_ZCLOUD_SHA"', text)

    def test_gate_composes_all_three_resilience_scenarios(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "RunnerSmokeTests.test_reconnect_adopts_and_persists_conversation",
            text,
        )
        self.assertIn(
            "TaskClaimTests.test_stale_claim_is_automatically_recovered",
            text,
        )
        self.assertIn(
            "ProjectRuntimeTests.test_expired_heavy_lease_is_recovered_after_owner_crash",
            text,
        )
        self.assertIn("node tests/test_firefox_recovery.js", text)
        self.assertIn("node tests/test_firefox_recovery_contract.js", text)
        self.assertIn("ZCLOUD_HOSTED_RESILIENCE_SIMULATION_GREEN=1", text)

    def test_gate_reacts_to_each_underlying_implementation_surface(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for path in (
            "server.py",
            "project_runtime.py",
            "firefox-extension/recovery.js",
            "firefox-extension/background.js",
            "public/zcloud-worker.user.js",
        ):
            self.assertGreaterEqual(
                text.count(f"      - {path}"),
                2,
                f"{path} must trigger both pull_request and push resilience gates",
            )

    def test_gate_cannot_mutate_live_vps_or_browser_state(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for forbidden in (
            "systemctl",
            "/home/ubuntu/zennay-cloud",
            "history.db",
            "sudo ",
            "ssh ",
            "scripts/zcloud_transactional_promote.py",
            "browser.tabs.remove",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
