"""Offline contract tests; no VPS or network access."""
import copy
import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ops_offline_admission_20261008.py"
spec = importlib.util.spec_from_file_location("offline_admission", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

MAIN = "a" * 40
HEAD = "b" * 40

def good():
    return {
        "canonical_repository": "Zennay/zCloud",
        "main_sha": MAIN, "candidate_base_sha": MAIN, "candidate_head_sha": HEAD,
        "serialized_gates": {"580": "released", "1089": "released"},
        "ownership_inventory_complete": True, "owner_overlap": False,
        "dashboard_recovery_pr_mutation": False,
        "checks": {key: {"status": "success", "head_sha": HEAD, "main_sha": MAIN}
                   for key in module.REQUIRED},
    }

class AdmissionTests(unittest.TestCase):
    def test_all_evidence_consistent(self):
        self.assertTrue(module.assess(good())["admissible"])

    def test_reject_mutations_and_stale_state(self):
        changes = (
            ("candidate_base_sha", "c" * 40),
            ("canonical_repository", "someone/fork"),
            ("ownership_inventory_complete", False),
            ("owner_overlap", True),
            ("dashboard_recovery_pr_mutation", True),
            ("serialized_gates", {"580": "released", "1089": "active"}),
            ("serialized_gates", {"580": "released"}),
            ("checks", {}),
            ("main_sha", "not-a-sha"),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                bundle = good()
                bundle[field] = value
                self.assertFalse(module.assess(bundle)["admissible"])

    def test_reject_each_failed_or_mixed_head_check(self):
        for name in module.REQUIRED:
            for key, value in (("status", "failure"), ("head_sha", "c" * 40),
                               ("main_sha", "c" * 40)):
                with self.subTest(name=name, key=key):
                    bundle = good()
                    bundle["checks"][name][key] = value
                    self.assertFalse(module.assess(bundle)["admissible"])

    def test_reject_malformed_bundle(self):
        for bundle in (None, [], {"main_sha": MAIN}, {"checks": None}):
            self.assertFalse(module.assess(bundle)["admissible"])

if __name__ == "__main__":
    unittest.main()
