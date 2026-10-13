"""Real zCloud HTTP adapter acceptance in a temporary, isolated SQLite environment.

Only reads actual /api/runner-targets and /api/worker-debug; the worker status
event fixture writes to an ephemeral test database, never a live VPS service.
"""
import json
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
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
