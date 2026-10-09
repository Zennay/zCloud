"""Contract tests for fail-closed worker evidence assessment."""
import datetime as dt
import unittest

from scripts.zcloud_worker_claim_evidence_gate import assess

NOW = dt.datetime(2026, 10, 9, 3, 0, tzinfo=dt.timezone.utc)


def snapshot(**changes):
    data = {
        "assignment_id": "assignment-1",
        "worker_id": "worker-1",
        "source": "trusted_runtime",
        "generation_started_at": "2026-10-09T02:58:00Z",
        "heartbeat_at": "2026-10-09T02:59:00Z",
    }
    data.update(changes)
    return data


class ClaimEvidenceTests(unittest.TestCase):
    def test_fresh_runtime_proof(self):
        self.assertEqual(assess(snapshot(), now=NOW)["state"], "generation_observed")

    def test_notion_status_does_not_prove_generation(self):
        self.assertEqual(assess(snapshot(source="notion"), now=NOW)["state"], "unverified")

    def test_heartbeat_expires(self):
        self.assertEqual(assess(snapshot(heartbeat_at="2026-10-09T02:50:00Z"), now=NOW)["state"], "stale")

    def test_future_heartbeat_fails_closed(self):
        self.assertEqual(assess(snapshot(heartbeat_at="2026-10-09T03:01:00Z"), now=NOW)["state"], "unverified")

    def test_generation_after_heartbeat_fails_closed(self):
        self.assertEqual(assess(snapshot(generation_started_at="2026-10-09T03:00:00Z"), now=NOW)["state"], "unverified")

    def test_missing_assignment_fails_closed(self):
        self.assertEqual(assess(snapshot(assignment_id=""), now=NOW)["state"], "unverified")

    def test_invalid_timestamp_fails_closed(self):
        self.assertEqual(assess(snapshot(heartbeat_at="garbage"), now=NOW)["state"], "unverified")

    def test_naive_timestamp_fails_closed(self):
        self.assertEqual(assess(snapshot(heartbeat_at="2026-10-09T02:59:00"), now=NOW)["state"], "unverified")

    def test_outputs_never_authorize_mutations(self):
        for s in (snapshot(), snapshot(source="notion"), snapshot(assignment_id="")):
            self.assertFalse(assess(s, now=NOW)["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
