"""Real zCloud HTTP adapter acceptance in a temporary, isolated SQLite environment.

Only reads actual /api/runner-targets and /api/worker-debug; the worker status
event fixture writes to an ephemeral test database, never a live VPS service.
"""
import json
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import server
from tests._worker_env import WorkerEnv
from scripts import zcloud_worker_generation_evidence_20261013_w1 as subject


class WorkerGenerationHTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = WorkerEnv().__enter__()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.base = "http://127.0.0.1:" + str(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.env.__exit__(None, None, None)

    def report(self):
        with patch.object(subject, "ENDPOINT", self.base):
            targets, debug = subject._live_snapshots()
        return subject.classify(targets, debug)

    def allocated_keys(self):
        # Allocation is the authority; priority can reorder providers across
        # projects, so never assume a fixed project is the Claude slot.
        with patch.object(subject, "ENDPOINT", self.base):
            current = subject._fetch("/api/runner-targets")
        workers = current["global_allocation"]["workers"]
        chatgpt = [w["worker_key"] for w in workers if w["provider"] == "chatgpt"]
        claude = [w["worker_key"] for w in workers if w["provider"] == "claude"]
        self.assertEqual(2, len(chatgpt), current)
        self.assertEqual(1, len(claude), current)
        return chatgpt, claude

    def emit(self, key, event="heartbeat", **kwargs):
        project, ordinal = key.split("::w")
        body = {
            "event": event,
            "at": datetime.now(timezone.utc).isoformat(),
            "projectId": key,
            "baseProjectId": project,
            "workerSlot": int(ordinal),
            "target": "https://chatgpt.com/c/6abe050d-c0e0-83ed-9ea6-ddc523a7b19f",
            **kwargs,
        }
        req = urllib.request.Request(
            self.base + "/api/runner-status",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body).encode(),
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_actual_server_does_not_report_availability_as_generation(self):
        proof = self.report()
        self.assertFalse(proof["all_allocated_chatgpt_generating"])
        self.assertEqual(3, proof["allocated_count"])
        self.assertFalse(proof["control_authorized"])
        self.assertFalse(proof["generation_directly_verified"])

    def test_two_chatgpt_heartbeats_and_one_claude_are_classified_correctly(self):
        chatgpt, claude = self.allocated_keys()
        for key in chatgpt:
            self.emit(key, generating=True, reason="violentmonkey-primary-runner")
        proof = self.report()
        self.assertTrue(proof["all_allocated_chatgpt_generating"], proof)
        observed = {row["worker"]: row["state"] for row in proof["workers"]}
        for key in chatgpt:
            self.assertEqual("generating_observed", observed[key])
        for key in claude:
            self.assertEqual("other_provider", observed[key])

    def test_real_http_progress_is_not_interchangeable_with_generating_heartbeat(self):
        chatgpt, _ = self.allocated_keys()
        for worker in chatgpt:
            self.emit(worker, generating=True, reason="violentmonkey-primary-runner")
        proof = self.report()
        self.assertTrue(proof["all_allocated_chatgpt_generating"])
        self.assertFalse(proof["all_allocated_chatgpt_progressing"])
        # A real browser driver's generated-text change sends this distinct
        # event, unlike the timer heartbeat which reports state only.
        for worker in chatgpt:
            self.emit(worker, event="generation-progress", generating=True,
                      reason="response-text-changed")
        proof = self.report()
        self.assertTrue(proof["all_allocated_chatgpt_progressing"], proof)
        self.assertTrue(all(row["progress_evidence"] for row in proof["workers"]
                            if row["state"] != "other_provider"))

    def test_real_http_single_worker_progress_does_not_green_entire_pool(self):
        chatgpt, _ = self.allocated_keys()
        for worker in chatgpt:
            self.emit(worker, generating=True, reason="violentmonkey-primary-runner")
        self.emit(chatgpt[0], event="generation-progress", generating=True,
                  reason="response-text-changed")
        proof = self.report()
        self.assertTrue(proof["all_allocated_chatgpt_generating"])
        self.assertFalse(proof["all_allocated_chatgpt_progressing"])

    def test_progress_superseded_by_timer_heartbeat_is_conservatively_unproven(self):
        """Document current backend last-event-only evidence limitation."""
        chatgpt, _ = self.allocated_keys()
        for worker in chatgpt:
            self.emit(worker, event="generation-progress", generating=True,
                      reason="response-text-changed")
        self.assertTrue(self.report()["all_allocated_chatgpt_progressing"])
        self.emit(chatgpt[0], event="heartbeat", generating=True,
                  reason="timer-heartbeat-following-progress")
        proof = self.report()
        self.assertTrue(proof["all_allocated_chatgpt_generating"])
        self.assertFalse(proof["all_allocated_chatgpt_progressing"])
        by_worker = {row["worker"]: row for row in proof["workers"]}
        self.assertFalse(by_worker[chatgpt[0]]["progress_evidence"])

    def test_actual_server_paused_worker_cannot_be_green_after_heartbeat(self):
        chatgpt, _ = self.allocated_keys()
        for key in chatgpt:
            self.emit(key, generating=True, reason="violentmonkey-primary-runner")
        self.assertTrue(self.report()["all_allocated_chatgpt_generating"])
        project, slot = chatgpt[0].rsplit("::w", 1)
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_workers SET desired_state='paused' "
                "WHERE project_id=? AND worker_slot=?",
                (project, int(slot)),
            )
        proof = self.report()
        self.assertFalse(proof["all_allocated_chatgpt_generating"])
        states = {row["worker"]: row["state"] for row in proof["workers"]}
        self.assertEqual("inactive_target", states[chatgpt[0]])

    def test_real_http_rejects_prior_browser_event_after_queue_reclaim(self):
        chatgpt, _ = self.allocated_keys()
        for key in chatgpt:
            self.emit(key, generating=True, reason="violentmonkey-primary-runner")
        self.assertTrue(self.report()["all_allocated_chatgpt_generating"])
        key = chatgpt[0]
        project, local_slot = key.rsplit("::w", 1)
        with patch.object(subject, "ENDPOINT", self.base):
            allocations = subject._fetch("/api/runner-targets")["global_allocation"]["workers"]
        claim_id = next(row["queue_id"] for row in allocations if row["worker_key"] == key)
        older_event = (datetime.now(timezone.utc) - timedelta(seconds=8)).isoformat()
        newer_claim = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_events SET ts=? WHERE project_id=? AND worker_slot=?",
                (older_event, project, int(local_slot)),
            )
            conn.execute(
                "UPDATE portfolio_queue SET claimed_at=? WHERE queue_id=?",
                (newer_claim, claim_id),
            )
        proof = self.report()
        self.assertFalse(proof["all_allocated_chatgpt_generating"])
        states = {row["worker"]: row["state"] for row in proof["workers"]}
        self.assertEqual("generation_predates_claim", states[key])

    def test_prompt_sent_after_generating_does_not_stay_green(self):
        chatgpt, _ = self.allocated_keys()
        for key in chatgpt:
            self.emit(key, generating=True, reason="violentmonkey-primary-runner")
        self.emit(chatgpt[-1], event="prompt-sent",
                  generating=False, reason="violentmonkey-command-push")
        proof = self.report()
        self.assertFalse(proof["all_allocated_chatgpt_generating"])
        observed = {row["worker"]: row["state"] for row in proof["workers"]}
        self.assertEqual("prompt_sent_without_generation", observed[chatgpt[-1]])


if __name__ == "__main__":
    unittest.main()
