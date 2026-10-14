"""Offline fail-closed tests: never exercise GitHub Actions or a runner."""
import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ops_workflow_run_static_screen_20261008.py"
spec = importlib.util.spec_from_file_location("deploy_ops_workflow_run_screen", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class WorkflowRunStaticScreenTests(unittest.TestCase):
    def assert_denied(self, source, verdict=None):
        result = mod.screen(source)
        if verdict:
            self.assertEqual(result["verdict"], verdict)
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["recovery_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_self_hosted_workflow_run(self):
        self.assert_denied("on:\n  workflow_run:\n    workflows: [build]\njobs:\n  repair:\n    runs-on: self-hosted\n", "REVIEW_REQUIRED")

    def test_workflow_run_write_permission(self):
        self.assert_denied("on: workflow_run\npermissions:\n  contents: write\n", "REVIEW_REQUIRED")

    def test_privileged_command(self):
        self.assert_denied("on: workflow_run\nsteps:\n  - run: sudo systemctl restart dashboard\n", "REVIEW_REQUIRED")

    def test_flow_style_multi_trigger(self):
        self.assert_denied("on: [push, workflow_run]\\njobs:\\n  repair:\\n    runs-on: self-hosted\\n", "REVIEW_REQUIRED")

    def test_quoted_trigger_key(self):
        self.assert_denied("'on':\\n  'workflow_run':\\n    types: [completed]\\njobs:\\n  run:\\n    runs-on: self-hosted\\n", "REVIEW_REQUIRED")

    def test_missing_trigger_is_unknown_not_safe(self):
        self.assert_denied("on: push\njobs:\n  read:\n    runs-on: ubuntu-latest\n", "UNKNOWN_DENY")

    def test_malformed_empty_is_unknown(self):
        self.assert_denied("", "UNKNOWN_DENY")

    def test_workflow_run_hosted_no_cues_is_still_not_authorized(self):
        self.assert_denied("on: workflow_run\njobs:\n  verify:\n    runs-on: ubuntu-latest\n", "UNKNOWN_DENY")

if __name__ == "__main__":
    unittest.main()
