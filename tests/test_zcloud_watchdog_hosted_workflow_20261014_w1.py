"""Hosted queue-observer workflow non-mutation source contract."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-watchdog-hosted-queue-readonly-20261014-w1.yml"


class HostedQueueWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WORKFLOW.read_text(encoding="utf-8")
        cls.code = "\n".join(line for line in cls.raw.splitlines()
                             if not line.lstrip().startswith("#"))

    def test_explicit_manual_only_no_indirect_trigger(self):
        self.assertRegex(self.raw, r"(?m)^on:\n  workflow_dispatch:\s*$")
        trigger_block = self.raw.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        for forbidden in ("pull_request:", "push:", "schedule:", "workflow_run:"):
            self.assertNotIn(forbidden, trigger_block)

    def test_job_requires_exact_owner_main_and_first_attempt(self):
        for gate in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.run_attempt == 1",
        ):
            self.assertIn(gate, self.code)

    def test_hosted_only_and_minimal_permissions(self):
        self.assertRegex(self.raw, r"(?m)^    runs-on: ubuntu-latest\s*$")
        self.assertNotRegex(self.code, r"runs-on:\s*(?:self-hosted|\[self-hosted)")
        permissions = self.raw.split("\npermissions:", 1)[1].split("\njobs:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertIn("actions: read", permissions)
        self.assertNotRegex(permissions, r"(?m):\s*write\s*$")
        self.assertIn("cancel-in-progress: false", self.raw)

    def test_checkout_immutable_without_stored_credentials(self):
        self.assertIn("ref: ${{ github.sha }}", self.raw)
        self.assertIn("persist-credentials: false", self.raw)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', self.raw)

    def test_main_verified_before_and_after_observation(self):
        start = self.raw.index('main_before="$(timeout 12s gh api --method GET')
        command = self.raw.index("python3 scripts/zcloud_watchdog_queue_collector_20261014_w1.py")
        final = self.raw.index('main_after="$(timeout 12s gh api --method GET')
        publish = self.raw.index("python3 scripts/zcloud_watchdog_operator_summary_20261014_w1.py")
        self.assertLess(start, command)
        self.assertLess(command, final)
        self.assertLess(final, publish)
        self.assertIn('test "$main_before" = "$GITHUB_SHA"', self.raw)
        self.assertIn('[[ "$main_after" != "$GITHUB_SHA" ]]', self.raw)

    def test_legacy_admission_and_route_triage_both_finish_before_main_recheck(self):
        a = self.raw.index("python3 scripts/zcloud_watchdog_queue_collector_20261014_w1.py")
        b = self.raw.index("python3 scripts/zcloud_queue_route_triage_20261014_w1.py")
        c = self.raw.index('main_after="$(timeout 12s gh api --method GET')
        d = self.raw.index("python3 scripts/zcloud_queue_route_summary_20261014_w1.py")
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertLess(c, d)

    def test_no_mutation_commands_or_privileged_runner(self):
        for forbidden in (
            "sudo ", "systemctl ", "restart ", "curl -X ", "gh workflow run",
            "gh run cancel", "api --method POST", "api --method PATCH",
            "api --method PUT", "api --method DELETE", "force-push",
            "actions: write", "contents: write",
        ):
            self.assertNotIn(forbidden, self.code)

    def test_timeout_and_sanitized_summary_are_required(self):
        self.assertIn("timeout-minutes: 6", self.code)
        self.assertIn("timeout --signal=TERM --kill-after=5s 125s", self.code)
        self.assertIn("timeout --signal=TERM --kill-after=5s 105s", self.code)
        self.assertIn("zcloud_queue_route_triage_20261014_w1.py", self.code)
        self.assertIn("zcloud_queue_route_summary_20261014_w1.py", self.code)
        self.assertIn("observer_summary_rc", self.code)
        self.assertIn("triage_summary_rc", self.code)
        self.assertIn("observer_summary_rc != 0 || triage_summary_rc != 0", self.code)
        self.assertIn("--collector-exit", self.code)
        self.assertIn("--summary-path", self.code)
        self.assertIn('GITHUB_STEP_SUMMARY', self.code)
        self.assertNotIn("cat $report", self.code)
        self.assertNotIn("tee $report", self.code)


if __name__ == "__main__":
    unittest.main()
