#!/usr/bin/env python3
"""Fail-closed, read-only release probe for dashboard recovery quarantine.

This test has no workflow dispatch, subprocess, network, or filesystem writes.
It is deliberately independent of the workflow implementation owner (#1135).
"""
import argparse
from pathlib import Path
import re
import sys
import unittest

WORKFLOW = Path(".github/workflows/zcloud-dashboard-access-recovery.yml")


def job_block(source: str, job_name: str) -> str:
    match = re.search(r"(?m)^  " + re.escape(job_name) + r":\s*\n", source)
    if not match:
        raise ValueError("missing job: " + job_name)
    tail = source[match.end():]
    next_job = re.search(r"(?m)^  [A-Za-z_][A-Za-z_0-9-]*:\s*\n", tail)
    return tail[:next_job.start()] if next_job else tail


def validate(source: str) -> list[str]:
    findings = []
    try:
        recovery = job_block(source, "recover")
    except ValueError:
        return ["recover job absent: cannot confirm quarantine contract"]
    # This is intentionally stricter than a branch-only guard. Quarantine means
    # *no event* may enter the legacy privileged job until owner admission.
    if not re.search(r"(?m)^    if:\s*(?:\$\{\{\s*)?false(?:\s*\}\})?\s*(?:#.*)?$", recovery):
        findings.append("recover job not unconditionally quarantined")
    try:
        external = job_block(source, "external_verify")
    except ValueError:
        findings.append("external hosted verification missing")
    else:
        if not re.search(r"(?m)^    runs-on:\s*ubuntu-latest\s*$", external):
            findings.append("external verification is not hosted")
        if re.search(r"(?m)^    if:\s*(?:\$\{\{\s*)?false", external):
            findings.append("external verification also disabled")
    return findings


class QuarantineContractTests(unittest.TestCase):
    SAFE = """jobs:
  recover:
    if: ${{ false }}
    runs-on: self-hosted
    steps:
      - run: sudo systemctl restart zennay-cloud
  external_verify:
    runs-on: ubuntu-latest
    steps:
      - run: echo status
"""

    def test_quarantined_recovery_is_accepted(self):
        self.assertEqual(validate(self.SAFE), [])

    def test_live_recovery_rejected(self):
        self.assertIn("recover job not unconditionally quarantined",
                      validate(self.SAFE.replace("    if: ${{ false }}\n", "")))

    def test_event_only_guard_rejected(self):
        self.assertIn("recover job not unconditionally quarantined",
                      validate(self.SAFE.replace("${{ false }}", "${{ github.event_name != 'pull_request' }}")))

    def test_hosted_probe_required(self):
        self.assertIn("external verification is not hosted",
                      validate(self.SAFE.replace("runs-on: ubuntu-latest", "runs-on: self-hosted")))

    def test_missing_external_probe_rejected(self):
        self.assertIn("external hosted verification missing",
                      validate(self.SAFE.split("  external_verify:")[0]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, default=WORKFLOW)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuarantineContractTests)
        sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    issues = validate(args.workflow.read_text(encoding="utf-8"))
    if issues:
        for issue in issues:
            print("DENY:", issue)
        sys.exit(1)
    print("PASS: legacy recovery is quarantined; external hosted probe remains enabled")
