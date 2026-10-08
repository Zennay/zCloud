#!/usr/bin/env python3
"""Fail-closed, dependency-free contract for PR-triggered dashboard recovery.

This test intentionally does NOT run the workflow or contact the VPS.
It rejects any pull_request-triggered workflow containing privileged recovery
operations, even when a job-level conditional is present. Splitting hosted PR
verification from trusted-main recovery is the supported remediation.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-dashboard-access-recovery.yml"
PR_TRIGGER = re.compile(r"(?m)^\s{0,4}(?:pull_request|pull_request_target)\s*:")
PRIVILEGED_OPERATION = re.compile(
    r"(?i)(?:\bsudo\b|\bsystemctl\s+(?:stop|restart|start)\b"
    r"|\b(?:chattr|chown|chmod)\b|\bsqlite3\b|\bactions/write\b)"
)


def violations(workflow_text: str) -> list[str]:
    # Conservative by design: cannot be defeated by changing a job's "if".
    if not PR_TRIGGER.search(workflow_text):
        return []
    return sorted(set(match.group(0) for match in PRIVILEGED_OPERATION.finditer(workflow_text)))


class DashboardRecoveryPRContract(unittest.TestCase):
    def test_unsafe_pr_trigger_is_rejected(self):
        example = """on:\n  pull_request:\n    branches: [main]\njobs:\n  recover:\n    steps:\n      - run: sudo systemctl restart zcloud\n"""
        self.assertTrue(violations(example))

    def test_read_only_pr_verification_is_accepted(self):
        example = """on:\n  pull_request:\n    branches: [main]\njobs:\n  external_verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: curl --fail https://example.invalid/status\n"""
        self.assertEqual(violations(example), [])

    def test_trusted_manual_recovery_requires_separate_trigger(self):
        example = """on:\n  workflow_dispatch:\njobs:\n  recover:\n    steps:\n      - run: sudo systemctl restart zcloud\n"""
        self.assertEqual(violations(example), [])

    def test_pull_request_target_must_not_have_privileged_recovery(self):
        example = "on:\\n  pull_request_target:\\njobs:\\n  recover:\\n    if: false\\n    steps:\\n      - run: sudo systemctl stop zcloud\\n"
        self.assertTrue(violations(example))

    def test_flow_style_event_list_is_not_a_bypass(self):
        example = "on: [workflow_dispatch, pull_request]\\njobs:\\n  recover:\\n    steps:\\n      - run: sudo chown root /tmp/example\\n"
        self.assertTrue(violations(example))

    def test_quoted_event_name_is_not_a_bypass(self):
        example = "on:\\n  'pull_request':\\njobs:\\n  recover:\\n    steps:\\n      - run: sudo chmod 600 /tmp/example\\n"
        self.assertTrue(violations(example))

    def test_live_workflow_prohibits_pr_privileged_recovery(self):
        self.assertTrue(WORKFLOW.is_file(), f"Expected workflow missing: {WORKFLOW}")
        self.assertEqual(
            violations(WORKFLOW.read_text(encoding="utf-8")),
            [],
            "PR-triggered privileged recovery is forbidden; split into read-only PR "
            "verification and trusted exact-main dispatch before release (#1135).",
        )


if __name__ == "__main__":
    unittest.main()
