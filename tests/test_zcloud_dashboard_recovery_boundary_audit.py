"""Tests for the non-authorizing dashboard recovery boundary inventory."""
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts/zcloud_dashboard_recovery_boundary_audit.py"
spec = importlib.util.spec_from_file_location("recovery_boundary", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class BoundaryTests(unittest.TestCase):
    def test_detects_serialized_mutations(self):
        result = module.inspect("pull_request:\n  jobs:\n    run: sudo -n systemctl restart zennay-cloud.service\n")
        self.assertTrue(result["requires_serialized_owner"])
        self.assertTrue(result["observed"]["untrusted_pr_trigger"])
        self.assertFalse(result["safe_for_parallel_dispatch"])
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["deploy_authorized"])

    def test_detects_sqlite_and_permissions(self):
        result = module.inspect("sudo -n chmod u+rw history.db\nconn.execute(\"BEGIN IMMEDIATE\")")
        self.assertTrue(result["observed"]["sqlite_write_probe"])
        self.assertTrue(result["observed"]["filesystem_mutation"])
        self.assertTrue(result["requires_serialized_owner"])

    def test_empty_input_does_not_infer_authorization(self):
        result = module.inspect("")
        self.assertFalse(result["requires_serialized_owner"])
        self.assertFalse(result["safe_for_parallel_dispatch"])
        self.assertFalse(result["workflow_dispatch_authorized"])

if __name__ == "__main__":
    unittest.main()
