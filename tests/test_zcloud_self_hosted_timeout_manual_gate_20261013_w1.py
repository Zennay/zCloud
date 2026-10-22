"""Regression contract for an owner-only manual VPS timeout-audit diagnostic.

Pure source checks: never connect to or mutate a live VPS.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-self-hosted-timeout-audit.yml"


def sections() -> tuple[str, str]:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count("\n  prove:\n") == 1, "ambiguous privileged job"
    hosted, vps = source.split("\n  prove:\n", 1)
    return hosted, vps


class ManualMainTimeoutAuditAdmissionTests(unittest.TestCase):
    def test_privileged_job_is_manual_owner_main_only(self):
        _, vps = sections()
        gate = re.search(r"^    if: >-\n((?:      [^\n]*\n)+)", vps, re.M)
        self.assertIsNotNone(gate, "privileged job missing explicit gate")
        expression = " ".join(line.strip() for line in gate.group(1).splitlines())
        self.assertEqual(
            expression,
            "github.event_name == 'workflow_dispatch' && "
            "github.repository == 'Zennay/zCloud' && "
            "github.actor == 'Zennay' && "
            "github.ref == 'refs/heads/main'",
        )
        self.assertNotIn("pull_request.head", expression)
        self.assertNotIn("||", expression)

    def test_pull_request_checks_are_hosted_only(self):
        hosted, vps = sections()
        self.assertIn("  pull_request:", hosted)
        self.assertIn("    runs-on: ubuntu-latest", hosted)
        self.assertNotRegex(hosted.split("\n  validate:\n", 1)[1], r"(?m)^    runs-on: .*self-hosted")
        self.assertIn("    runs-on: [self-hosted, zcloud, vps]", vps)
        self.assertIn("persist-credentials: false", vps)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", vps)

    def test_manual_dispatch_guards_fresh_main_before_any_live_audit(self):
        _, vps = sections()
        guard = vps.index("scripts/zcloud_vps_runner_guard.py --json")
        recheck = vps.index("Revalidate exact live canonical main before VPS inspection")
        audit = vps.index("Audit self-hosted job timeout bounds read only")
        self.assertLess(guard, recheck)
        self.assertLess(recheck, audit)
        self.assertIn('test "$GITHUB_REF" = \'refs/heads/main\'', vps)
        self.assertIn('test "$GITHUB_ACTOR" = \'Zennay\'', vps)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', vps)
        self.assertIn(
            "timeout 15 git ls-remote https://github.com/Zennay/zCloud.git refs/heads/main",
            vps,
        )
        self.assertIn('test -n "$current_main"', vps)
        self.assertIn('test "$current_main" = "$EXPECTED_SHA"', vps)
        self.assertNotIn("|| true", vps[vps.index("Revalidate exact"):audit])

    def test_read_only_proof_is_bounded(self):
        hosted, vps = sections()
        self.assertIn("timeout-minutes: 4", hosted)
        self.assertIn("timeout-minutes: 5", vps)
        self.assertIn("permissions:\n  contents: read", hosted)
        for forbidden in ("sudo ", "systemctl restart", "git push", "actions: write", "contents: write"):
            self.assertNotIn(forbidden, hosted + vps)

    def test_hosted_validation_runs_both_contract_suites(self):
        hosted, vps = sections()
        command = (
            "python3 -m unittest -v tests.test_zcloud_self_hosted_timeout_audit "
            "tests.test_zcloud_self_hosted_timeout_manual_gate_20261013_w1"
        )
        self.assertIn(command, hosted)
        self.assertIn(command, vps)
        self.assertIn("tests/test_zcloud_self_hosted_timeout_manual_gate_20261013_w1.py", hosted)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", hosted)


if __name__ == "__main__":
    unittest.main()
