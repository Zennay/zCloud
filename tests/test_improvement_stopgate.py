import json
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
enhancements_stub.load_resource_policy = lambda: {}
sys.modules["enhancements"] = enhancements_stub

import server


class ImprovementStopGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-stopgate-")
        self.original_db = server.DB
        self.original_cache = server.CACHE
        server.DB = Path(self.tmp.name) / "history.db"
        server.CACHE = None
        server.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DB = self.original_db
        server.CACHE = self.original_cache
        self.tmp.cleanup()

    def request(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def project_shape(self, project_id):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT active,worker_count,conversation_id FROM runner_targets WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return tuple(row)

    def test_canonical_main_sha_prefers_remote_main_over_stale_local_ref(self):
        remote_sha = "b" * 40
        stale_sha = "a" * 40
        def fake_cmd(args):
            if "ls-remote" in args:
                return f"{remote_sha}\trefs/heads/main"
            return stale_sha
        with patch.object(server, "cmd", side_effect=fake_cmd):
            self.assertEqual(remote_sha, server.canonical_main_sha())

    def test_canonical_main_sha_tolerates_remote_and_shallow_ref_failure(self):
        sha = "a" * 40
        def fake_cmd(args):
            if "ls-remote" in args:
                raise RuntimeError("network unavailable")
            ref = args[-1]
            if ref in ("origin/main", "main"):
                raise RuntimeError("missing shallow ref")
            return sha
        with patch.object(server, "cmd", side_effect=fake_cmd):
            self.assertEqual(sha, server.canonical_main_sha())

    def test_two_clean_reviews_finish_only_cloud_and_survive_restart(self):
        ftmo_before = self.project_shape("ftmo")
        first = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        self.assertEqual("running", first["state"])
        self.assertEqual(1, first["clean_reviews"])

        second = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        self.assertEqual("finished", second["state"])
        self.assertFalse(second["auto_continue"])
        self.assertEqual("two_consecutive_clean_senior_reviews", second["stop_reason"])
        self.assertTrue(second["last_green_commit"])

        server.init_db()
        persisted = server.improvement_loop_state("cloud")
        self.assertEqual("finished", persisted["state"])
        self.assertEqual(ftmo_before, self.project_shape("ftmo"))

        targets = server.runner_worker_targets()
        self.assertFalse(targets["cloud::w1"]["auto_continue"])
        self.assertTrue(targets["ftmo::w1"]["auto_continue"])

    def test_open_review_resets_clean_review_streak(self):
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        state = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=False, p0p1_open=True
        )
        self.assertEqual("running", state["state"])
        self.assertEqual(0, state["clean_reviews"])

    def test_late_open_review_reopens_clean_finish_without_resetting_iteration_history(self):
        for _ in range(4):
            server.improvement_loop_record("cloud", "iteration")
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        finished = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        self.assertEqual("finished", finished["state"])
        self.assertEqual(4, finished["iteration_count"])
        green_commit = finished["last_green_commit"]

        reopened = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=False, p0p1_open=True
        )
        self.assertEqual("running", reopened["state"])
        self.assertTrue(reopened["auto_continue"])
        self.assertEqual(4, reopened["iteration_count"])
        self.assertEqual(0, reopened["clean_reviews"])
        self.assertIsNone(reopened["stop_reason"])
        self.assertEqual(green_commit, reopened["last_green_commit"])

    def test_api_open_review_reopens_clean_finish(self):
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        status, body = self.request(
            "/api/improvement-loop",
            {
                "project_id": "cloud",
                "action": "review",
                "finish_gate_green": False,
                "p0p1_open": True,
            },
        )
        self.assertEqual(200, status, body)
        self.assertEqual("running", body["improvement"]["state"])
        self.assertEqual(0, body["improvement"]["clean_reviews"])

    def test_open_review_does_not_reopen_hard_limit_audit_finish(self):
        for _ in range(10):
            server.improvement_loop_record("cloud", "iteration")
        audited = server.improvement_loop_record("cloud", "audit", audit_green=True)
        self.assertEqual("finished", audited["state"])
        opened = server.improvement_loop_record(
            "cloud", "review", finish_gate_green=False, p0p1_open=True
        )
        self.assertEqual("finished", opened["state"])
        self.assertFalse(opened["auto_continue"])
        self.assertEqual(10, opened["iteration_count"])
        self.assertEqual("hard_iteration_limit_final_audit_green", opened["stop_reason"])
        self.assertEqual("green", opened["audit_result"])

    def test_hard_limit_requires_final_audit_then_finishes(self):
        for _ in range(10):
            state = server.improvement_loop_record("cloud", "iteration")
        self.assertEqual("audit_required", state["state"])
        self.assertEqual(10, state["iteration_count"])
        self.assertFalse(state["auto_continue"])

        audited = server.improvement_loop_record("cloud", "audit", audit_green=True)
        self.assertEqual("finished", audited["state"])
        self.assertEqual("green", audited["audit_result"])
        self.assertEqual("hard_iteration_limit_final_audit_green", audited["stop_reason"])

    def test_finished_loop_blocks_push_and_self_improvement_claim_but_not_other_projects(self):
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )
        server.improvement_loop_record(
            "cloud", "review", finish_gate_green=True, p0p1_open=False
        )

        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(409, status, body)
        self.assertIn("Finished / Maintain", body["error"])

        blocked = server.task_claim_acquire(
            "cloud", "next-improvement", "owner-a", "cloud::w1", 60,
            {"loop": "self_improvement"},
        )
        self.assertFalse(blocked["acquired"])
        self.assertEqual("improvement_loop_finished", blocked["blocked"])

        maintenance = server.task_claim_acquire(
            "cloud", "maintenance", "owner-a", "cloud::w1", 60,
            {"loop": "maintenance"},
        )
        self.assertTrue(maintenance["acquired"])

        other = server.task_claim_acquire(
            "ftmo", "independent", "owner-b", "ftmo::w1", 60,
            {"loop": "self_improvement"},
        )
        self.assertTrue(other["acquired"])

    def test_runner_marker_events_drive_persistent_gate(self):
        payload = {
            "projectId": "cloud::w1",
            "baseProjectId": "cloud",
            "workerSlot": 1,
            "target": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "title": "zCloud",
        }
        server.runner_record({**payload, "event": "improvement-review-green"})
        self.assertEqual(1, server.improvement_loop_state("cloud")["clean_reviews"])
        server.runner_record({**payload, "event": "improvement-review-green"})
        self.assertEqual("finished", server.improvement_loop_state("cloud")["state"])

    def test_non_cloud_marker_cannot_stop_cloud(self):
        before = server.improvement_loop_state("cloud")
        server.runner_record({
            "projectId": "ftmo::w1",
            "baseProjectId": "ftmo",
            "workerSlot": 1,
            "event": "improvement-review-green",
            "target": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "title": "FTMO",
        })
        after = server.improvement_loop_state("cloud")
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["clean_reviews"], after["clean_reviews"])

    def test_cloud_prompt_contains_bounded_finish_protocol(self):
        prompt = server.project_worker_prompt("cloud", "zCloud", "base", 1, 1)
        self.assertIn("ZCLOUD_ITERATION_COMPLETE", prompt)
        self.assertIn("ZCLOUD_FINISH_REVIEW: GREEN_NO_P0P1", prompt)
        self.assertIn("ZCLOUD_FINAL_AUDIT: GREEN", prompt)
        self.assertIn("tiende/harde laatste iteratie", prompt)

    def test_resume_resets_gate_without_changing_conversation(self):
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_targets SET conversation_id=? WHERE project_id='cloud'",
                ("12345678-1234-1234-1234-123456789abc",),
            )
        for _ in range(10):
            server.improvement_loop_record("cloud", "iteration")
        status, body = self.request(
            "/api/improvement-loop", {"project_id": "cloud", "action": "resume"}
        )
        self.assertEqual(200, status, body)
        state = body["improvement"]
        self.assertEqual("running", state["state"])
        self.assertTrue(state["auto_continue"])
        self.assertEqual(0, state["iteration_count"])
        self.assertEqual(0, state["clean_reviews"])
        self.assertEqual(
            "12345678-1234-1234-1234-123456789abc",
            self.project_shape("cloud")[2],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
