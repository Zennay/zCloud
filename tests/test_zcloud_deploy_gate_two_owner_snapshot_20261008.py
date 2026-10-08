import datetime as dt
import unittest

from scripts.zcloud_deploy_gate_two_owner_snapshot_20261008 import assess

NOW = dt.datetime(2026, 10, 8, 0, 35, tzinfo=dt.timezone.utc)
SHA = "b8bd568435390eebc4d47fae4c4c2d749fe4a90b"

def snapshot():
    return {
        "repository": "Zennay/zCloud",
        "main_sha": SHA,
        "collected_at": "2026-10-08T00:34:00Z",
        "inventory_complete": True,
        "other_serialized_owners_clear": True,
        "gates": [
            {"id": "#580", "status": "released", "verified_main_sha": SHA},
            {"id": "#1089", "status": "released", "verified_main_sha": SHA},
        ],
    }

class TestDeployGate(unittest.TestCase):
    def test_exact_complete_release(self):
        self.assertEqual(assess(snapshot(), now=NOW), ("clear", "all_serialized_owners_released"))

    def test_active_owner_blocks(self):
        s = snapshot()
        s["gates"][1]["status"] = "active"
        self.assertEqual(assess(s, now=NOW)[0], "blocked")

    def test_stale_evidence_fails_closed(self):
        s = snapshot()
        s["collected_at"] = "2026-10-08T00:20:00Z"
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_future_evidence_fails_closed(self):
        s = snapshot()
        s["collected_at"] = "2026-10-08T00:36:00Z"
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_duplicate_gate_fails_closed(self):
        s = snapshot()
        s["gates"][1]["id"] = "#580"
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_missing_other_owner_evidence_fails_closed(self):
        s = snapshot()
        s.pop("other_serialized_owners_clear")
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_cross_revision_gate_fails_closed(self):
        s = snapshot()
        s["gates"][1]["verified_main_sha"] = "a" * 40
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_main_drift_fails_closed(self):
        self.assertEqual(
            assess(snapshot(), now=NOW, expected_main_sha="a" * 40),
            ("incomplete", "main_moved_since_collection"),
        )

    def test_exact_main_matches(self):
        self.assertEqual(
            assess(snapshot(), now=NOW, expected_main_sha=SHA)[0], "clear"
        )

    def test_missing_gate_fails_closed(self):
        s = snapshot()
        s["gates"].pop()
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_unknown_status_fails_closed(self):
        s = snapshot()
        s["gates"][0]["status"] = "closed"
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

    def test_incomplete_inventory_fails_closed(self):
        s = snapshot()
        s["inventory_complete"] = False
        self.assertEqual(assess(s, now=NOW)[0], "incomplete")

if __name__ == "__main__":
    unittest.main()
