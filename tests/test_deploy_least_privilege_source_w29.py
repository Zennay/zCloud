"""Offline source guard for production deployment workflow privilege boundaries.

Static tripwire only; this does not authorize deployment or model YAML execution.
Run: python3 -m unittest discover -s tests -p 'test_deploy_least_privilege_source_w29.py' -v
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-vps-deploy.yml"


class DeployLeastPrivilegeSourceW29(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_only_trusted_regression_completion_triggers(self):
        header = self.source.split("\npermissions:", 1)[0]
        self.assertIn("workflow_run:", header)
        self.assertIn("workflows: [\"zCloud regression smoke\"]", header)
        self.assertIn("types: [completed]", header)
        self.assertIn("branches: [main]", header)
        for event in ("pull_request:", "pull_request_target:", "workflow_dispatch:", "repository_dispatch:", "issue_comment:"):
            self.assertNotIn(event, header)

    def test_minimal_explicit_top_level_token_permissions(self):
        match = re.search(r"(?m)^permissions:\s*\n((?:^[ ]{2}[^\n]+\n)+)", self.source)
        self.assertIsNotNone(match, "explicit top-level permissions required")
        entries = [line.strip() for line in match.group(1).splitlines()]
        self.assertEqual(entries, ["actions: read", "contents: read", "statuses: write"])
        self.assertNotIn("permissions: write-all", self.source)

    def test_candidate_requires_successful_main_regression(self):
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.source)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.source)
        self.assertIn("github.run_attempt == 1", self.source)

    def test_self_hosted_deploy_depends_on_preflight(self):
        deploy = self.source.split("\n  deploy:", 1)[1]
        self.assertRegex(deploy, r"(?m)^    needs: preflight$")
        self.assertIn("needs.preflight.result == 'success'", deploy)
        self.assertIn("needs.preflight.outputs.deploy == 'true'", deploy)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", deploy)

    def test_never_cancel_running_production_transaction(self):
        self.assertIn("group: zcloud-production-deploy", self.source)
        self.assertRegex(self.source, r"(?m)^  cancel-in-progress: false$")

if __name__ == "__main__":
    unittest.main()
