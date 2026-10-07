"""Static, dependency-free safety contract for the canonical production deploy trigger.

This is intentionally read-only. It does not start a workflow, deploy, or mutate
the production control plane. Run with:
    python -m unittest discover -s tests -p 'test_zcloud_deploy_trigger_contract.py'
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-vps-deploy.yml"


class DeployTriggerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_trigger_only_completed_main_regression(self):
        source = self.source
        self.assertRegex(source, r"(?m)^\s*workflow_run:\s*$")
        self.assertRegex(source, r'(?m)^\s*workflows:\s*\[["\']zCloud regression smoke["\']\]')
        self.assertRegex(source, r"(?m)^\s*types:\s*\[completed\]")
        self.assertRegex(source, r"(?m)^\s*branches:\s*\[main\]")
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", source)
        self.assertIn("github.run_attempt == 1", source)

    def test_deploy_is_serialized_without_canceling_active_production(self):
        source = self.source
        self.assertRegex(source, r"(?m)^concurrency:\s*$")
        self.assertRegex(source, r"(?m)^\s*group:\s*zcloud-production-deploy\s*$")
        self.assertRegex(source, r"(?m)^\s*cancel-in-progress:\s*false\s*$")

    def test_preflight_cannot_unconditionally_launch_deploy(self):
        source = self.source
        self.assertIn("needs: preflight", source)
        self.assertIn("needs.preflight.outputs.deploy == 'true'", source)
        self.assertIn('context") == "zcloud/vps-production"', source)
        self.assertIn('already_green = bool(', source)
        self.assertIn('decision = "false" if already_green else "true"', source)

    def test_production_runner_is_explicit(self):
        self.assertRegex(self.source, r"(?m)^\s*runs-on:\s*\[self-hosted, zcloud, vps\]\s*$")
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.source)


if __name__ == "__main__":
    unittest.main()
