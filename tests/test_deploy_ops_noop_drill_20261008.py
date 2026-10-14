"""Regression checks for the offline, non-authorizing recovery tabletop evaluator."""
import importlib.util
import unittest
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ops_noop_drill_20261008.py"
spec = importlib.util.spec_from_file_location("deploy_ops_noop_drill_20261008", TARGET)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

REF = "https://github.com/Zennay/zCloud/actions/runs/123"


def record(scenario):
    return {"scenario": scenario, "expected_stop": True, "observed_stop": True, "evidence_refs": [REF]}


class TabletopDrillTests(unittest.TestCase):
    def test_three_stop_scenarios(self):
        scenarios = [record(s) for s in sorted(module.SCENARIOS)]
        self.assertEqual({r["scenario"] for r in scenarios}, module.SCENARIOS)
        self.assertTrue(all(module.check(r) for r in scenarios))

    def test_non_stop_rejected(self):
        item = record("stale_head_ci")
        item["observed_stop"] = False
        self.assertFalse(module.check(item))

    def test_unknown_scenario_rejected(self):
        self.assertFalse(module.check(record("restart_production")))

    def test_unknown_field_rejected(self):
        item = record("missing_external_health")
        item["token"] = "not-permitted"
        self.assertFalse(module.check(item))

    def test_untrusted_evidence_url_rejected(self):
        item = record("serialized_owner_active")
        item["evidence_refs"] = ["https://example.org/private"]
        self.assertFalse(module.check(item))

    def test_missing_evidence_rejected(self):
        item = record("stale_head_ci")
        item["evidence_refs"] = []
        self.assertFalse(module.check(item))

    def test_authorizations_never_true(self):
        for status in (True, False):
            output = module.result(status)
            for key in ("release_authorized", "merge_authorized", "deploy_authorized", "rollback_authorized", "mutation_performed"):
                self.assertIs(output[key], False)


if __name__ == "__main__":
    unittest.main()
