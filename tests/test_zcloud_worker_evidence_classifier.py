"""Fail-closed regression for a standalone worker evidence classifier."""
import unittest
from datetime import datetime, timezone, timedelta
from scripts.zcloud_worker_evidence_classifier import classify_worker_evidence

NOW = datetime(2026, 10, 9, 4, 15, tzinfo=timezone.utc)

def sample(kind="generation-started", **overrides):
    event = {"worker_id": "worker-1", "assignment_id": "task-1", "event": kind, "ts": NOW.isoformat()}
    event.update(overrides)
    return {"worker_id": "worker-1", "assignment_id": "task-1", "events": [event]}

class WorkerEvidenceTests(unittest.TestCase):
    def check(self, value, expected):
        result = classify_worker_evidence(value, now=NOW)
        self.assertEqual(result["state"], expected)
        self.assertIs(result["recovery_authorized"], False)

    def test_missing_signals(self):
        self.check({"worker_id": "worker-1", "assignment_id": "task-1"}, "unknown")
        self.check({"worker_id": "worker-1", "assignment_id": "task-1", "events": []}, "unknown")

    def test_prompt_only_never_proves_generation(self):
        self.check(sample("prompt-sent"), "attempted")

    def test_correlated_generation(self):
        self.check(sample(), "generating")

    def test_unrelated_worker(self):
        self.check(sample(worker_id="worker-2"), "unknown")

    def test_unrelated_assignment(self):
        self.check(sample(assignment_id="task-2"), "unknown")

    def test_expired(self):
        self.check(sample(ts=(NOW - timedelta(minutes=5)).isoformat()), "unknown")

    def test_future_timestamp(self):
        self.check(sample(ts=(NOW + timedelta(seconds=1)).isoformat()), "unknown")

    def test_malformed_timestamp(self):
        self.check(sample(ts="not-a-date"), "unknown")

    def test_naive_timestamp(self):
        self.check(sample(ts="2026-10-09T04:15:00"), "unknown")

    def test_completed_does_not_prove_materiality(self):
        self.check(sample("generation-completed"), "completed")

    def test_completion_does_not_authorize_recovery(self):
        result = classify_worker_evidence(sample("generation-completed"), now=NOW)
        self.assertNotEqual(result["state"], "material_progress")
        self.assertFalse(result["recovery_authorized"])

if __name__ == "__main__":
    unittest.main()
