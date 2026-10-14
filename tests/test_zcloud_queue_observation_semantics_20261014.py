"""Execute the hosted queue observer against synthetic GitHub GET responses.

Never calls GitHub, touches a VPS or reads production worker state.
"""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
from unittest import mock
import unittest
from urllib.parse import parse_qs, urlparse

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-hosted-emergency-queue-sweep.yml").read_text(encoding="utf-8")


def embedded_script():
    stage = SOURCE.split("- name: Summarize sampled Actions queue with GET only", 1)[1]
    section = stage.split("          python3 - <<'PY'\n", 1)[1]
    return "\n".join(line[10:] for line in section.split("\n          PY", 1)[0].splitlines())


def job(job_id, *, age_minutes, status="queued", labels=None, runner_id=None, stamp=True):
    now = datetime.now(timezone.utc)
    record = {
        "id": job_id,
        "status": status,
        "labels": ["self-hosted", "zcloud", "vps"] if labels is None else labels,
        "runner_id": runner_id,
    }
    if stamp:
        record["created_at"] = (
            now - timedelta(minutes=age_minutes)
        ).isoformat().replace("+00:00", "Z")
    return record


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.body


def observe(queued=None, in_progress=None, *, totals=None, jobs=None):
    queued = queued if queued is not None else []
    in_progress = in_progress if in_progress is not None else []
    jobs = jobs if jobs is not None else {}
    totals = totals if totals is not None else {}
    requests = []

    def mocked_open(request, timeout=0):
        assert request.get_method() == "GET", "only GET is allowed"
        assert timeout == 12
        parsed = urlparse(request.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "api.github.com"
        assert parsed.path.startswith("/repos/Zennay/zCloud/actions/runs")
        requests.append(parsed.path)
        if parsed.path.endswith("/jobs"):
            run_id = int(parsed.path.split("/")[6])
            rows = jobs.get(run_id, [])
            return Response({"total_count": len(rows), "jobs": rows})
        status = parse_qs(parsed.query)["status"][0]
        rows = queued if status == "queued" else in_progress
        return Response({"total_count": totals.get(status, len(rows)), "workflow_runs": rows})

    output = io.StringIO()
    with (mock.patch("urllib.request.urlopen", side_effect=mocked_open),
          mock.patch.dict(os.environ, {"GH_TOKEN": "synthetic_token",
                                      "GITHUB_REPOSITORY": "Zennay/zCloud"}),
          redirect_stdout(output)):
        exec(compile(embedded_script(), "<offline-queue-observer>", "exec"),
             {"__name__": "__main__"})
    text = output.getvalue()
    assert text.startswith("ZCLOUD_QUEUE_READONLY=")
    return json.loads(text.split("=", 1)[1]), requests, text


class QueuedJobTimestampSemanticsTests(unittest.TestCase):
    def test_job_creation_overrides_old_workflow_creation(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        result, requests, _ = observe(
            queued=[{"id": 100, "created_at": old}],
            jobs={100: [job(1, age_minutes=1)]})
        self.assertEqual(1, result["queued_jobs"])
        self.assertEqual(0, result["stale_queued_jobs"])
        self.assertEqual(0, result["stale_unassigned_jobs"])
        self.assertTrue(result["sample_complete"])
        self.assertEqual(3, len(requests))

    def test_stale_unassigned_queue_job_is_not_overlooked(self):
        result, _, _ = observe(
            queued=[{"id": 100, "created_at": "irrelevant"}],
            jobs={100: [job(1, age_minutes=21, runner_id=None)]})
        self.assertEqual(1, result["stale_queued_jobs"])
        self.assertEqual(1, result["stale_unassigned_jobs"])
        self.assertEqual(0, result["queued_with_assigned_runner"])

    def test_assigned_queued_job_is_not_a_missing_listener(self):
        result, _, _ = observe(
            queued=[{"id": 100}],
            jobs={100: [job(1, age_minutes=21, runner_id=401)]})
        self.assertEqual(1, result["stale_queued_jobs"])
        self.assertEqual(0, result["stale_unassigned_jobs"])
        self.assertEqual(1, result["queued_with_assigned_runner"])

    def test_missing_or_invalid_job_timestamp_is_not_fresh_evidence(self):
        for stamp in ("missing", "invalid"):
            with self.subTest(stamp=stamp):
                row = job(1, age_minutes=20, stamp=(stamp != "missing"))
                if stamp == "invalid":
                    row["created_at"] = "yesterday maybe"
                result, _, _ = observe(
                    queued=[{"id": 100}], jobs={100: [row]})
                self.assertFalse(result["sample_complete"])
                self.assertEqual(1, result["queue_age_unknown_jobs"])
                self.assertEqual(0, result["stale_unassigned_jobs"])

    def test_missing_job_rows_are_not_complete_queue_proof(self):
        result, _, _ = observe(queued=[{"id": 100}], jobs={100: []})
        self.assertEqual(1, result["runs_with_no_job_records"])
        self.assertFalse(result["sample_complete"])
        self.assertEqual(0, result["stale_unassigned_jobs"])

    def test_malformed_labels_and_runner_id_fail_closed(self):
        for row in (
            job(1, age_minutes=12, labels="self-hosted"),
            job(2, age_minutes=12, runner_id=True),
            job(3, age_minutes=12, runner_id=-3),
        ):
            with self.subTest(row=row["id"]):
                result, _, _ = observe(queued=[{"id": 100}], jobs={100: [row]})
                self.assertFalse(result["sample_complete"])
                self.assertGreaterEqual(result["indeterminate_job_rows"], 1)
                self.assertEqual(0, result["stale_unassigned_jobs"])

    def test_truncation_is_always_reported_incomplete(self):
        result, requests, _ = observe(queued=[{"id": 100}], totals={"queued": 30},
                                      jobs={100: [job(1, age_minutes=2)]})
        self.assertFalse(result["sample_complete"])
        self.assertEqual(1, result["sampled_runs"])
        self.assertEqual(3, len(requests))

    def test_non_self_hosted_is_excluded_from_listener_inference(self):
        result, _, _ = observe(queued=[{"id": 100}],
                              jobs={100: [job(1, age_minutes=30, labels=["ubuntu-latest"])]})
        self.assertTrue(result["sample_complete"])
        self.assertEqual(0, result["stale_unassigned_jobs"])
        self.assertEqual(0, result["sampled_jobs"])

    def test_redacted_get_only_output_contains_no_raw_identifiers(self):
        result, urls, output = observe(
            queued=[{"id": 123456789, "created_at": "legacy-secret"}],
            jobs={123456789: [job(191919191, age_minutes=12)]})
        self.assertTrue(all(url.startswith("/repos/Zennay/zCloud/actions/runs") for url in urls))
        self.assertFalse(result["mutation_performed"])
        self.assertNotIn("123456789", output)
        self.assertNotIn("191919191", output)
        self.assertNotIn("synthetic_token", output)
        self.assertNotIn("legacy-secret", output)


if __name__ == "__main__":
    unittest.main()
