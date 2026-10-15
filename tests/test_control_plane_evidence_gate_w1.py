"""Offline negative-case coverage for control-plane evidence admission."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from control_plane_evidence_gate_w1 import assess

HEAD = "a" * 40
BASE = "b" * 40

def valid():
    return {
        "head_sha": HEAD, "base_sha": BASE, "collision_free": True,
        "integration_owner_approved": True, "mutation_authorized": False,
        "checks": [{"name": "full-regression", "head_sha": HEAD,
                    "url": "https://github.com/Zennay/zCloud/actions/runs/123",
                    "status": "completed", "conclusion": "success",
                    "expected_failures": 0}],
    }

class EvidenceGateTests(unittest.TestCase):
    def test_valid_offline_manifest(self):
        self.assertTrue(assess(valid())["accepted"])

    def test_no_manifest(self):
        self.assertFalse(assess(None)["accepted"])

    def test_stale_head(self):
        m = valid()
        m["checks"][0]["head_sha"] = BASE
        self.assertFalse(assess(m)["accepted"])

    def test_nonterminal_or_failed(self):
        for status, conclusion in [("queued", None), ("in_progress", None),
                                   ("completed", "failure"), ("completed", "skipped")]:
            with self.subTest(status=status, conclusion=conclusion):
                m = valid()
                m["checks"][0].update(status=status, conclusion=conclusion)
                self.assertFalse(assess(m)["accepted"])

    def test_expected_failures_not_green(self):
        for count in (1, 13, True):
            with self.subTest(count=count):
                m = valid()
                m["checks"][0]["expected_failures"] = count
                self.assertFalse(assess(m)["accepted"])

    def test_fail_closed_approval_ownership_and_mutation(self):
        for key, value in [("collision_free", False), ("integration_owner_approved", False),
                           ("mutation_authorized", True)]:
            with self.subTest(key=key):
                m = valid()
                m[key] = value
                self.assertFalse(assess(m)["accepted"])

    def test_missing_and_duplicate_checks(self):
        m = valid()
        m["checks"] = []
        self.assertFalse(assess(m)["accepted"])
        m = valid()
        m["checks"].append(dict(m["checks"][0]))
        self.assertFalse(assess(m)["accepted"])

    def test_malformed_sha_url_and_check(self):
        for field, value in [("head_sha", "short"), ("base_sha", "x" * 40)]:
            m = valid()
            m[field] = value
            self.assertFalse(assess(m)["accepted"])
        m = valid()
        m["checks"][0]["url"] = "https://example.com/123"
        self.assertFalse(assess(m)["accepted"])
        m = valid()
        m["checks"][0] = None
        self.assertFalse(assess(m)["accepted"])

if __name__ == "__main__":
    unittest.main()
