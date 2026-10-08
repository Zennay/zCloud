"""Offline queue-capacity validator safety contract."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/zcloud_deploy_ops_queue_capacity_check_20261008.py"
spec = importlib.util.spec_from_file_location("capacity", MODULE)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)

SHA = "a" * 40


def example():
    return {
        "main_sha": SHA,
        "candidate": {"head_sha": "b" * 40, "paths": ["docs/new.md"], "owner": "worker-1", "checks_green_for_head": True},
        "open_prs": [],
        "pr_less_branches": [],
        "open_prs_complete": True,
        "branches_complete": True,
        "serialized_gate_released": True,
    }


class CapacityContractTests(unittest.TestCase):
    def test_complete_snapshot_is_review_only_never_authorized(self):
        result = capacity.evaluate(example())
        self.assertEqual(result["decision"], "REVIEW_ONLY")
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])

    def test_partial_pr_list_stops(self):
        doc = example()
        doc["open_prs_complete"] = False
        self.assertIn("partial_inventory", capacity.evaluate(doc)["reasons"])

    def test_partial_branch_list_stops(self):
        doc = example()
        doc["branches_complete"] = False
        self.assertEqual(capacity.evaluate(doc)["decision"], "STOP")

    def test_serialized_gate_stops(self):
        doc = example()
        doc["serialized_gate_released"] = False
        self.assertIn("serialized_gate_held", capacity.evaluate(doc)["reasons"])

    def test_branch_overlap_stops(self):
        doc = example()
        doc["pr_less_branches"] = [{"owner": "worker-2", "paths": ["docs/new.md"]}]
        self.assertIn("path_overlap", capacity.evaluate(doc)["reasons"])

    def test_pr_overlap_stops(self):
        doc = example()
        doc["open_prs"] = [{"owner": "worker-3", "paths": ["docs/new.md"]}]
        self.assertIn("path_overlap", capacity.evaluate(doc)["reasons"])

    def test_stale_head_checks_stop(self):
        doc = example()
        doc["candidate"]["checks_green_for_head"] = False
        self.assertIn("missing_exact_head_checks", capacity.evaluate(doc)["reasons"])

    def test_unexpected_field_stops(self):
        doc = example()
        doc["unknown"] = True
        self.assertEqual(capacity.evaluate(doc)["reasons"], ["invalid_schema"])

    def test_bad_sha_stops(self):
        doc = example()
        doc["main_sha"] = "truncated"
        self.assertIn("invalid_main_sha", capacity.evaluate(doc)["reasons"])

    def test_duplicate_paths_stop(self):
        doc = example()
        doc["candidate"]["paths"] = ["docs/new.md", "docs/new.md"]
        self.assertIn("invalid_paths", capacity.evaluate(doc)["reasons"])

    def test_missing_owner_stops(self):
        doc = example()
        doc["candidate"]["owner"] = ""
        self.assertIn("missing_owner", capacity.evaluate(doc)["reasons"])


if __name__ == "__main__":
    unittest.main()
