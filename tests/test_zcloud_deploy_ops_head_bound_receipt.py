"""Fail-closed contract for deploy-ops head-bound evidence."""
import unittest
from scripts.zcloud_deploy_ops_head_bound_receipt import evaluate


def valid():
    sha = "a" * 40
    return {"candidate_pr": 1125, "owner": "worker-1", "main_sha": "b" * 40,
            "head_sha": sha, "ahead": 1, "behind": 0, "changed_paths": ["docs/example.md"],
            "required_checks": [{"name": "regression", "run_id": 123, "head_sha": sha, "conclusion": "success"}],
            "serialized_owners": {"580": "released", "1089": "released"},
            "open_pr_overlap": False, "prless_branch_overlap": False}


class ReceiptTests(unittest.TestCase):
    def test_complete_evidence_never_authorizes_mutation(self):
        result = evaluate(valid())
        self.assertEqual(result["decision"], "evidence_complete_non_authorizing")
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_missing_fields_defer(self):
        d = valid()
        del d["required_checks"]
        self.assertEqual(evaluate(d)["decision"], "defer")

    def test_old_head_check_defer(self):
        d = valid()
        d["required_checks"][0]["head_sha"] = "c" * 40
        self.assertEqual(evaluate(d)["decision"], "defer")

    def test_pending_check_defer(self):
        d = valid()
        d["required_checks"][0]["conclusion"] = "queued"
        self.assertEqual(evaluate(d)["decision"], "defer")

    def test_main_drift_defer(self):
        d = valid()
        d["behind"] = 1
        self.assertEqual(evaluate(d)["decision"], "defer")

    def test_each_gate_independent(self):
        for gate in ("580", "1089"):
            with self.subTest(gate=gate):
                d = valid()
                d["serialized_owners"][gate] = "open"
                self.assertEqual(evaluate(d)["decision"], "defer")

    def test_overlaps_defer(self):
        for field in ("open_pr_overlap", "prless_branch_overlap"):
            with self.subTest(field=field):
                d = valid()
                d[field] = True
                self.assertEqual(evaluate(d)["decision"], "defer")

    def test_absent_checks_do_not_pass(self):
        d = valid()
        d["required_checks"] = []
        self.assertEqual(evaluate(d)["decision"], "defer")

    def test_boolean_ahead_not_integer(self):
        d = valid()
        d["ahead"] = True
        self.assertEqual(evaluate(d)["decision"], "defer")


if __name__ == "__main__":
    unittest.main()
