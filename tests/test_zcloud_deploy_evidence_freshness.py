"""Offline regression for deploy evidence binding."""
import unittest
from scripts.zcloud_deploy_evidence_freshness import inspect

A = "a" * 40
B = "b" * 40


class EvidenceTest(unittest.TestCase):
    def sample(self):
        return {"main_sha": A, "candidate_base_sha": A,
                "candidate_head_sha": B, "ahead": 1, "behind": 0,
                "serialized_gate_released": True,
                "checks": {name: {"conclusion": "success", "head_sha": B}
                           for name in ("regression", "cpu", "dashboard")}}

    def test_valid_snapshot_is_not_a_deploy_authorization(self):
        self.assertEqual(inspect(self.sample()), [])

    def test_main_drift_rejected(self):
        s = self.sample()
        s["main_sha"] = B
        self.assertIn("candidate base differs from current main", inspect(s))

    def test_old_head_success_rejected(self):
        s = self.sample()
        s["checks"]["regression"]["head_sha"] = A
        self.assertTrue(any("not bound" in x for x in inspect(s)))

    def test_gate_closed_rejected(self):
        s = self.sample()
        s["serialized_gate_released"] = False
        self.assertTrue(inspect(s))

    def test_missing_checks_rejected(self):
        s = self.sample()
        del s["checks"]["dashboard"]
        self.assertTrue(inspect(s))

    def test_same_head_as_main_rejected(self):
        s = self.sample()
        s["candidate_head_sha"] = A
        for check in s["checks"].values():
            check["head_sha"] = A
        self.assertTrue(any("must differ" in x for x in inspect(s)))

    def test_zero_or_boolean_ahead_rejected(self):
        for invalid in (0, False, -1, None):
            s = self.sample()
            s["ahead"] = invalid
            self.assertTrue(inspect(s))

    def test_boolean_behind_rejected(self):
        s = self.sample()
        s["behind"] = False
        self.assertTrue(inspect(s))


if __name__ == "__main__":
    unittest.main()
