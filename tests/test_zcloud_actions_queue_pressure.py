from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.zcloud_actions_queue_pressure import (
    PressureInputError,
    classify_pressure,
    load_snapshot,
)


NOW = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


def stamp(seconds_ago: int = 0) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def job(status: str, *, age: int, labels=None):
    return {
        "status": status,
        "created_at": stamp(age),
        "labels": labels or ["self-hosted", "zcloud", "vps"],
    }


def snapshot(*jobs, capacity=1, source_complete=True):
    return {
        "schema_version": 1,
        "captured_at": stamp(),
        "capacity": capacity,
        "source_complete": source_complete,
        "runs": [
            {
                "workflow_path": ".github/workflows/example-proof.yml",
                "status": "in_progress",
                "created_at": stamp(120),
                "jobs": list(jobs),
            }
        ],
    }


class ActionsQueuePressureTests(unittest.TestCase):
    def test_healthy_without_waiting_jobs(self):
        result = classify_pressure(snapshot(job("in_progress", age=40)))
        self.assertEqual(result["state"], "healthy")
        self.assertEqual(result["headroom"], 0)
        self.assertFalse(result["mutation_performed"])

    def test_bounded_queue_when_capacity_is_available(self):
        result = classify_pressure(
            snapshot(job("queued", age=75), capacity=2)
        )
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["waiting_self_hosted_jobs"], 1)
        self.assertEqual(result["oldest_wait_seconds"], 75)

    def test_saturated_when_runner_is_busy_and_work_waits(self):
        result = classify_pressure(
            snapshot(job("in_progress", age=100), job("queued", age=80))
        )
        self.assertEqual(result["state"], "saturated")
        self.assertIn("NO_IMMEDIATE_RUNNER_HEADROOM", result["reasons"])

    def test_stalled_queue_takes_precedence_after_fifteen_minutes(self):
        result = classify_pressure(snapshot(job("queued", age=901)))
        self.assertEqual(result["state"], "stalled")
        self.assertEqual(result["oldest_wait_seconds"], 901)

    def test_incomplete_snapshot_is_fail_visible(self):
        result = classify_pressure(
            snapshot(job("queued", age=30), source_complete=False)
        )
        self.assertEqual(result["state"], "incomplete")
        self.assertIn("SOURCE_INCOMPLETE", result["reasons"])

    def test_non_zcloud_self_hosted_jobs_are_ignored(self):
        result = classify_pressure(
            snapshot(
                job("queued", age=500, labels=["self-hosted", "ftmo-research"]),
                capacity=1,
            )
        )
        self.assertEqual(result["state"], "healthy")
        self.assertEqual(result["waiting_self_hosted_jobs"], 0)

    def test_unknown_fields_and_unsafe_workflow_paths_fail_closed(self):
        bad = snapshot()
        bad["hidden"] = True
        with self.assertRaisesRegex(PressureInputError, "snapshot_schema_invalid"):
            classify_pressure(bad)

        bad = snapshot()
        bad["runs"][0]["workflow_path"] = "../steal.yml"
        with self.assertRaisesRegex(PressureInputError, "workflow_path_invalid"):
            classify_pressure(bad)

    def test_bool_capacity_and_future_job_timestamp_are_rejected(self):
        with self.assertRaisesRegex(PressureInputError, "capacity_invalid"):
            classify_pressure(snapshot(capacity=True))

        bad = snapshot(job("queued", age=0))
        bad["runs"][0]["jobs"][0]["created_at"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
        with self.assertRaisesRegex(PressureInputError, "job_created_after_capture"):
            classify_pressure(bad)

    def test_waiting_workflow_output_is_bounded_to_basename(self):
        payload = snapshot(job("queued", age=50), capacity=2)
        payload["runs"][0]["workflow_path"] = ".github/workflows/zcloud-test-proof.yml"
        result = classify_pressure(payload)
        self.assertEqual(result["waiting_workflows"], ["zcloud-test-proof.yml"])

    def test_symlink_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "snapshot.json"
            target.write_text(json.dumps(snapshot()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(PressureInputError, "input_path_invalid"):
                load_snapshot(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
