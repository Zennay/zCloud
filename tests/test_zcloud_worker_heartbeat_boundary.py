import datetime as dt
import unittest
from scripts.zcloud_worker_heartbeat_boundary import validate

NOW = dt.datetime(2026, 10, 9, 4, 0, tzinfo=dt.timezone.utc)

def sample():
    return dict(source="vps_sqlite_observed", kind="worker_generation_heartbeat",
                worker_id="worker-1", assignment_id="task-1", generation_id="gen-1",
                observed_at="2026-10-09T03:59:00Z", generation_started=True)

class WorkerHeartbeatBoundaryTests(unittest.TestCase):
    def test_valid_recent_observation(self):
        self.assertTrue(validate(sample(), NOW)["trusted"])
    def test_prompt_sent_is_not_generation(self):
        row = sample(); row["generation_started"] = False
        self.assertIn("generation_not_proven", validate(row, NOW)["reasons"])
    def test_browser_claim_is_not_sqlite_observation(self):
        row = sample(); row["source"] = "notion_worker_claim"
        self.assertIn("untrusted_source", validate(row, NOW)["reasons"])
    def test_stale_rejected(self):
        row = sample(); row["observed_at"] = "2026-10-09T03:56:59Z"
        self.assertIn("stale_observation", validate(row, NOW)["reasons"])
    def test_future_rejected(self):
        row = sample(); row["observed_at"] = "2026-10-09T04:00:01Z"
        self.assertIn("future_observation", validate(row, NOW)["reasons"])
    def test_malformed_record_rejected(self):
        self.assertFalse(validate([], NOW)["trusted"])
    def test_timezone_not_accepted(self):
        row = sample(); row["observed_at"] = "2026-10-09T05:59:00+02:00"
        self.assertIn("invalid_observed_at", validate(row, NOW)["reasons"])
    def test_missing_identity_denied(self):
        row = sample(); row.pop("assignment_id")
        self.assertIn("invalid_assignment_id", validate(row, NOW)["reasons"])
    def test_no_side_effects_claim(self):
        self.assertFalse(validate(sample(), NOW)["mutation_performed"])

if __name__ == "__main__":
    unittest.main()
