#!/usr/bin/env python3
"""Offline fixtures for the deploy recovery PR trust audit."""
import importlib.util
import pathlib
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_pr_recovery_trust_audit.py"
spec = importlib.util.spec_from_file_location("deploy_recovery_audit", MODULE)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class RecoveryTrustTests(unittest.TestCase):
    def test_untrusted_pr_privileged_recovery_denied(self):
        workflow = """on:
  pull_request:
    branches: [main]
jobs:
  recover:
    runs-on: self-hosted
    steps:
      - run: sudo systemctl restart zennay-cloud.service
"""
        report = audit_module.audit(workflow)
        self.assertEqual(report["status"], "unsafe")
        self.assertFalse(report["mutation_authorized"])
        self.assertFalse(report["deploy_authorized"])

    def test_hosted_read_only_pr_verification(self):
        workflow = """on:
  pull_request:
    branches: [main]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS https://example.invalid/status
"""
        report = audit_module.audit(workflow)
        self.assertEqual(report["status"], "clear")

    def test_hosted_mutating_pr_is_not_safe(self):
        workflow = """on:\n  pull_request:\njobs:\n  recovery:\n    runs-on: ubuntu-latest\n    steps:\n      - run: sudo systemctl stop zennay-cloud.service\n"""
        self.assertEqual(audit_module.audit(workflow)["status"], "unsafe")

    def test_unclassified_self_hosted_pr_fails_closed(self):
        workflow = """on:
  pull_request:
jobs:
  probe:
    runs-on: self-hosted
    steps:
      - run: echo hello
"""
        self.assertEqual(audit_module.audit(workflow)["status"], "unsafe")

    def test_manual_only_recovery_is_not_pr_exposed(self):
        workflow = """on:
  workflow_dispatch:
jobs:
  recover:
    runs-on: self-hosted
    steps:
      - run: sudo systemctl restart zennay-cloud.service
"""
        report = audit_module.audit(workflow)
        self.assertEqual(report["status"], "clear")
        self.assertEqual(report["pr_triggers"], [])

    def test_inline_trigger_fails_closed(self):
        self.assertEqual(audit_module.audit("on: [pull_request]\\njobs:\\n")["status"], "unknown")

    def test_missing_trigger_inventory_fails_closed(self):
        self.assertEqual(audit_module.audit("jobs:\\n  probe:\\n    runs-on: self-hosted\\n")["status"], "unknown")

    def test_no_jobs_on_pr_fails_closed(self):
        self.assertEqual(audit_module.audit("on:\n  pull_request:\n")["status"], "unsafe")


if __name__ == "__main__":
    unittest.main()
