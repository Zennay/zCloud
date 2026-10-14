"""Offline source guard for serialization of the production deploy workflow.

These checks are structural regression tripwires, not a deploy authorization
or substitute for YAML parsing, exact-head CI, or production acceptance.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-vps-deploy.yml"


def global_section(source: str, name: str) -> str:
    """Return only a top-level mapping block, never a nested job lookalike."""
    match = re.search(rf"(?m)^{re.escape(name)}:\s*\n", source)
    if match is None:
        raise AssertionError(f"missing top-level {name} block")
    remainder = source[match.end():]
    boundary = re.search(r"(?m)^[A-Za-z][A-Za-z_-]*:\s*(?:\n|$)", remainder)
    return remainder[:boundary.start()] if boundary else remainder


class ProductionDeploySerializationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_global_serialization_is_non_cancelling(self):
        section = global_section(self.source, "concurrency")
        self.assertRegex(section, r"(?m)^  group:\s*zcloud-production-deploy\s*$")
        self.assertRegex(section, r"(?m)^  cancel-in-progress:\s*false\s*$")
        self.assertNotRegex(section, r"(?m)^  cancel-in-progress:\s*true\s*$")

    def test_serialization_precedes_jobs(self):
        self.assertLess(
            self.source.index("\nconcurrency:\n"),
            self.source.index("\njobs:\n"),
        )

    def test_upstream_event_is_successful_main_regression(self):
        trigger = global_section(self.source, "on")
        self.assertRegex(trigger, r"(?m)^  workflow_run:\s*$")
        self.assertRegex(trigger, r"(?m)^    workflows:\s*\[\"zCloud regression smoke\"\]\s*$")
        self.assertRegex(trigger, r"(?m)^    types:\s*\[completed\]\s*$")
        self.assertRegex(trigger, r"(?m)^    branches:\s*\[main\]\s*$")

    def test_preflight_guards_successful_first_attempt_on_main(self):
        preflight = self.source.split("\n  preflight:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        self.assertIn("github.run_attempt == 1", preflight)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", preflight)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", preflight)

    def test_deploy_waits_for_preflight(self):
        deploy = self.source.split("\n  deploy:\n", 1)[1]
        self.assertRegex(deploy, r"(?m)^    needs:\s*preflight\s*$")
        self.assertIn("needs.preflight.outputs.deploy == 'true'", deploy)


if __name__ == "__main__":
    unittest.main()
