import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.zcloud_worker_start_blockers import (
    classify_worker_start_blockers,
    fetch_status,
)


def status_fixture(*, firefox_active=True, memory_healthy=True, worker=None):
    worker = worker or {
        "worker_id": "cloud::w1",
        "desired_state": "running",
        "state": "live",
        "current_task": {"claim_key": "q1"},
        "last_event": {"event": "heartbeat"},
    }
    return {
        "chatgpt_firefox": {"active": firefox_active},
        "dynamic_workers": {
            "memory_guard": {
                "healthy_for_new_worker": memory_healthy,
                "new_worker_capacity": 2 if memory_healthy else 0,
            }
        },
        "chatgpt_runners": {
            "cloud": {"workers": [worker]},
        },
    }


class WorkerStartBlockerTests(unittest.TestCase):
    def test_healthy_worker_is_startable(self):
        report = classify_worker_start_blockers(status_fixture())
        row = report["workers"][0]
        self.assertTrue(row["startable"])
        self.assertEqual([], row["blockers"])
        self.assertIsNone(row["primary_blocker"])

    def test_browser_and_resource_and_claim_are_distinct(self):
        worker = {
            "worker_id": "cloud::w1",
            "desired_state": "running",
            "state": "offline",
            "current_task": None,
            "last_event": {"event": "heartbeat"},
        }
        report = classify_worker_start_blockers(
            status_fixture(
                firefox_active=False,
                memory_healthy=False,
                worker=worker,
            )
        )
        blockers = {(item["category"], item["code"]) for item in report["workers"][0]["blockers"]}
        self.assertIn(("browser", "firefox_runtime_unavailable"), blockers)
        self.assertIn(("resource", "new_worker_memory_admission_blocked"), blockers)
        self.assertIn(("claim", "no_active_task_or_claim"), blockers)
        self.assertEqual("browser", report["workers"][0]["primary_blocker"]["category"])

    def test_assignment_invalid_is_project_context(self):
        worker = {
            "worker_id": "supa::w2",
            "desired_state": "running",
            "state": "starting",
            "current_task": {"claim_key": "q2"},
            "last_event": {"event": "assignment-invalid", "reason": "wrong project binding"},
        }
        report = classify_worker_start_blockers(status_fixture(worker=worker))
        self.assertEqual(
            {"category": "project_context", "code": "assignment_contract_invalid"},
            report["workers"][0]["primary_blocker"],
        )

    def test_auth_text_is_classified_but_never_emitted(self):
        secret = "login required for private-session@example.invalid token=SECRET"
        worker = {
            "worker_id": "ftmo::w1",
            "desired_state": "running",
            "state": "stale",
            "current_task": {"claim_key": "q3", "title": "secret task"},
            "last_event": {
                "event": "assignment-refresh-failed",
                "reason": secret,
                "error": secret,
            },
            "error": secret,
            "conversation_id": "private-conversation-id",
        }
        report = classify_worker_start_blockers(status_fixture(worker=worker))
        row = report["workers"][0]
        self.assertIn(
            {"category": "auth", "code": "session_or_auth_required"},
            row["blockers"],
        )
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("secret task", serialized)
        self.assertNotIn("private-conversation-id", serialized)

    def test_paused_and_draining_are_intentional_not_misdiagnosed(self):
        for desired in ("paused", "draining"):
            with self.subTest(desired=desired):
                worker = {
                    "worker_id": "cloud::w1",
                    "desired_state": desired,
                    "state": desired,
                    "current_task": None,
                    "last_event": {"event": "assignment-invalid"},
                }
                report = classify_worker_start_blockers(
                    status_fixture(
                        firefox_active=False,
                        memory_healthy=False,
                        worker=worker,
                    )
                )
                row = report["workers"][0]
                self.assertEqual("intentional_state", row["primary_blocker"]["category"])
                self.assertEqual(1, len(row["blockers"]))

    def test_unknown_shapes_fail_closed_without_raw_payload(self):
        report = classify_worker_start_blockers(
            {
                "chatgpt_firefox": "bad",
                "dynamic_workers": [],
                "chatgpt_runners": {
                    "cloud": {
                        "workers": [
                            {
                                "worker_id": "cloud::w1",
                                "desired_state": "running",
                                "state": "unknown",
                                "last_event": {"error": "opaque-secret"},
                            }
                        ]
                    }
                },
            }
        )
        row = report["workers"][0]
        self.assertFalse(row["startable"])
        self.assertIn(
            {"category": "browser", "code": "firefox_runtime_unavailable"},
            row["blockers"],
        )
        self.assertNotIn("opaque-secret", json.dumps(report))

    def test_status_fixture_cli_source_can_be_loaded_without_http(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps(status_fixture()), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(classify_worker_start_blockers(loaded)["workers"][0]["startable"])

    def test_fetch_status_retries_transient_failure(self):
        payload = json.dumps(status_fixture()).encode()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = payload

        calls = {"count": 0}

        def fake_open(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("transient")
            return response

        with patch("scripts.zcloud_worker_start_blockers.urllib.request.urlopen", side_effect=fake_open), \
             patch("scripts.zcloud_worker_start_blockers.time.sleep", return_value=None):
            # json.load needs a file-like object; use a tiny real implementation here.
            import io
            def fake_open_file(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError("transient")
                handle = io.BytesIO(payload)
                class Context:
                    def __enter__(self): return handle
                    def __exit__(self, *exc): return False
                return Context()
            with patch("scripts.zcloud_worker_start_blockers.urllib.request.urlopen", side_effect=fake_open_file):
                status = fetch_status("http://example.invalid", attempts=2, timeout=0.1)

        self.assertEqual(2, calls["count"])
        self.assertIn("chatgpt_runners", status)


if __name__ == "__main__":
    unittest.main()
