"""End-to-end: a real HTTP server + a simulated browser worker speaking the same API as the drivers.

Flow under test: /api/runner-targets -> validator -> /api/runner-status (heartbeat / prompt-sent /
send-blocked) -> /api/worker-debug verdicts -> /api/dynamic-workers/force-push -> /api/runner-commands
-> /api/runner-command-result.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

import server
from tests._worker_env import WorkerEnv


def utc():
    return datetime.now(timezone.utc).isoformat()


class WorkerDebugE2ETests(unittest.TestCase):
    def setUp(self):
        self.env = WorkerEnv().__enter__()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.env.__exit__(None, None, None)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read())

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def event(self, worker, name, **extra):
        base, slot = worker.split("::w")
        payload = {"event": name, "at": utc(), "projectId": worker, "baseProjectId": base, "workerSlot": int(slot),
                   "target": "https://chatgpt.com/c/6abe050d-c0e0-83ed-9ea6-ddc523a7b19f"}
        payload.update(extra)
        status, _ = self.post("/api/runner-status", payload)
        self.assertEqual(200, status)

    def verdicts(self):
        report = self.get("/api/worker-debug")
        return {row["worker"]: row for row in report["workers"]}, report

    def test_full_worker_lifecycle_is_observable(self):
        # 1. the worker asks for its target exactly like the drivers do
        targets = self.get("/api/runner-targets")["projects"]
        active = {k: v for k, v in targets.items() if v["active"]}
        self.assertEqual({"cloud::w1", "haxlab::w1", "raiseai::w1"}, set(active))
        for key, cfg in active.items():
            self.assertEqual([], server.worker_contract_failures(cfg), key)
            self.assertIn("Jij bent Worker", cfg["prompt"], key)

        # 2. no browser has reported yet -> explicit verdict instead of silence
        rows, report = self.verdicts()
        self.assertEqual("no-heartbeat", rows["cloud::w1"]["verdict"])
        self.assertEqual(3, len(rows))

        # 3. heartbeat while generating -> generating
        self.event("cloud::w1", "heartbeat", generating=True, reason="violentmonkey-primary-runner")
        rows, _ = self.verdicts()
        self.assertEqual("generating", rows["cloud::w1"]["verdict"])

        # 4. prompt-sent (not generating yet) -> prompt-sent
        self.event("haxlab::w1", "prompt-sent", reason="violentmonkey-command-push")
        rows, _ = self.verdicts()
        self.assertEqual("prompt-sent", rows["haxlab::w1"]["verdict"])
        self.assertIsNotNone(rows["haxlab::w1"]["last_prompt_age_s"])

        # 5. the browser refuses to send -> its exact reason is surfaced
        self.event("raiseai::w1", "send-blocked", reason="assignment-invalid:prompt-missing-worker-line")
        rows, report = self.verdicts()
        self.assertEqual("blocked:client", rows["raiseai::w1"]["verdict"])
        self.assertIn("prompt-missing-worker-line", rows["raiseai::w1"]["reason"])
        self.assertGreaterEqual(report["events_15m"].get("send-blocked", 0), 1)

    def test_force_push_round_trip(self):
        status, body = self.post("/api/dynamic-workers/force-push", {})
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])
        queued = {r["worker"]: r for r in body["results"] if r["queued"]}
        self.assertEqual({"cloud::w1", "haxlab::w1", "raiseai::w1"}, set(queued))

        # the drivers poll /api/runner-commands and must see one push per worker
        commands = self.get("/api/runner-commands")["commands"]
        self.assertEqual({"cloud::w1", "haxlab::w1", "raiseai::w1"}, {c["project_id"] for c in commands})
        self.assertTrue(all(c["action"] == "push" for c in commands))

        # idempotent: a second force push re-uses the pending commands instead of spamming
        _, again = self.post("/api/dynamic-workers/force-push", {})
        self.assertTrue(all(r.get("deduplicated") for r in again["results"]))
        self.assertEqual(3, len(self.get("/api/runner-commands")["commands"]))

        # driver reports the result -> command leaves the pending list
        first = commands[0]["id"]
        status, _ = self.post("/api/runner-command-result", {"command_id": first, "status": "completed", "result": "prompt sent"})
        self.assertEqual(200, status)
        self.assertEqual(2, len(self.get("/api/runner-commands")["commands"]))

    def test_broken_prompt_contract_is_reported_not_silent(self):
        original = server.project_worker_prompt
        server.project_worker_prompt = lambda *a, **k: original(*a, **k).replace("Jij bent ", "")
        try:
            rows, _ = self.verdicts()
            for worker, row in rows.items():
                self.assertEqual("blocked:contract", row["verdict"], worker)
                self.assertIn("prompt-missing-worker-line", row["contract_failures"], worker)
            status, body = self.post("/api/dynamic-workers/force-push", {})
            self.assertFalse(body["ok"])
            self.assertTrue(all((not r["queued"]) and "prompt-missing-worker-line" in r["reason"] for r in body["results"]))
            self.assertEqual([], self.get("/api/runner-commands")["commands"], "must not enqueue pushes that would be refused")
        finally:
            server.project_worker_prompt = original

    def test_paused_worker_is_reported_as_paused_and_not_pushed(self):
        with server.connect() as c:
            c.execute("UPDATE runner_workers SET desired_state='paused' WHERE project_id='haxlab'")
        rows, _ = self.verdicts()
        self.assertEqual("paused", rows["haxlab::w1"]["verdict"])
        _, body = self.post("/api/dynamic-workers/force-push", {})
        pushed = {r["worker"] for r in body["results"] if r["queued"]}
        self.assertNotIn("haxlab::w1", pushed)


if __name__ == "__main__":
    unittest.main()
