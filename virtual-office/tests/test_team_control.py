"""Expandable team, worker control room and expert proposals. Offline: zCloud is replaced by a fake."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import office_control as control
import office_experts as experts
import office_features as features
import office_game as game
import office_staff as staff
import server
import test_office

THREAD = "https://chatgpt.com/c/0123456789abcdef0123"
TARGETS = {"projects": {
    "haxlab-1": {"base_project_id": "haxlab", "active": True, "desired_state": "running", "provider": "chatgpt", "worker_slot": 1,
                 "queue_item": {"queue_id": "haxlab-aaa", "priority": "P2"}},
    "ftmo-1": {"base_project_id": "ftmo", "active": True, "desired_state": "paused", "provider": "chatgpt", "worker_slot": 1}},
    "max_workers": 4}
STATUS = {"projects": [{"id": "haxlab", "name": "Haxlab", "resource": {"priority": "normal"}},
                       {"id": "ftmo", "name": "FTMO", "resource": {"priority": "high"}},
                       {"id": "cloud", "name": "zCloud", "resource": {"priority": "normal"}}]}
QUEUE = {"items": [{"queue_id": "haxlab-aaa", "project_id": "haxlab", "title": "Bouw de arena", "priority": "P2", "status": "claimed"},
                   {"queue_id": "ftmo-bbb", "project_id": "ftmo", "title": "Valideer de strategie", "priority": "P1", "status": "queued"}]}


class FakeZ:
    def __init__(self):
        self.calls, self.targets, self.fail = [], json.loads(json.dumps(TARGETS)), None

    def __call__(self, path, payload=None, timeout=3.5, actor=""):
        self.calls.append((path, payload, actor))
        if self.fail and payload is not None:
            raise RuntimeError(self.fail)
        if path == "/api/runner-targets":
            return self.targets
        if path == "/api/status":
            return STATUS
        if path.startswith("/api/portfolio-queue") and payload is None:
            return QUEUE
        return {"ok": True, "command_id": 7}


class TeamControlTests(test_office.OfficeTests):
    def setUp(self):
        super().setUp()
        control.reset_cache()
        self.z = FakeZ()
        patcher = patch.object(server, "zcloud", self.z)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- team
    def test_defaults_seeded_and_extendable(self):
        self.assertEqual(sorted(features.PROFILE_IDS), ["lina", "maya", "noah"])
        out = staff.upsert(server, {"id": "ravi", "name": "Ravi", "title": "Data-expert", "focus": "analyse en cijfers", "project_id": "ftmo",
                                    "look": {"skin": "#8d5f43", "style": "bun"}, "voice": {"pitch": 0.9, "gender": "m"}})
        ravi = next(s for s in out["staff"] if s["id"] == "ravi")
        self.assertEqual(ravi["slot"], 3)
        self.assertEqual(ravi["desk"], {"x": 13.5, "z": -8.0})
        self.assertIn("ravi", features.PROFILE_IDS)
        self.assertIn("Je bent Ravi", server.PERSONAS["ravi"]["persona_prompt"])
        self.assertIn("akkoord", server.PERSONAS["ravi"]["persona_prompt"])
        job = server.enqueue("ravi", "task", "Bouw een rapportage")
        self.assertEqual(job["employee"], "ravi")
        self.assertEqual(server.build_portfolio_payload(job)["project_id"], "ftmo")
        server.init_db()   # restart: the new expert survives
        self.assertIn("ravi", features.PROFILE_IDS)

    def test_staff_validation_limit_and_retire(self):
        for bad in ({"id": "X"}, {"id": "a b", "name": "A"}, {"id": "ok1", "name": ""},
                    {"id": "ok1", "name": "A", "color": "red"}, {"id": "ok1", "name": "A", "project_id": "../x"}):
            with self.assertRaises(ValueError):
                staff.upsert(server, bad)
        for i in range(5):
            staff.upsert(server, {"id": "ex%d" % i, "name": "Expert %d" % i})
        self.assertEqual(len(features.PROFILE_IDS), 8)
        with self.assertRaisesRegex(ValueError, "passen"):
            staff.upsert(server, {"id": "extra", "name": "Te veel"})
        staff.upsert(server, {"id": "ex0", "active": False})
        self.assertNotIn("ex0", features.PROFILE_IDS)
        with self.assertRaises(ValueError):
            server.enqueue("ex0", "task", "Doe iets nuttigs")
        staff.upsert(server, {"id": "extra", "name": "Nu wel"})
        self.assertEqual(next(s for s in staff.list_staff(server) if s["id"] == "extra")["slot"], 3)   # takes the freed desk
        slots = [s["slot"] for s in staff.list_staff(server)]
        self.assertEqual(len(slots), len(set(slots)))

    def test_relay_job_carries_persona(self):
        features.relay_poll(server, {"employee": "noah", "client_id": "c", "conversation": THREAD, "ready": False})
        features.chat_enqueue(server, {"employee": "noah", "instruction": "Hoe staat Supa ervoor?"})
        job = features.relay_poll(server, {"employee": "noah", "client_id": "c", "conversation": THREAD, "ready": True})["job"]
        self.assertIn("Je bent Noah", job["persona"])

    # ---- control
    def test_overview_normalizes_zcloud(self):
        state = control.overview(server)
        self.assertTrue(state["online"])
        self.assertEqual([w["worker"] for w in state["workers"]], ["ftmo-1", "haxlab-1"])
        self.assertEqual(state["workers"][1]["queue_id"], "haxlab-aaa")
        self.assertEqual({p["id"]: p["priority"] for p in state["projects"]}["ftmo"], "high")
        self.assertEqual(len(state["queue"]), 2)
        self.assertIn("haxlab-aaa", control.digest(state))

    def test_overview_degrades_when_zcloud_down(self):
        def down(*a, **k):
            raise ConnectionError("weg")
        with patch.object(server, "zcloud", down):
            state = control.overview(server, fresh=True)
        self.assertFalse(state["online"])
        self.assertEqual(state["workers"], [])

    def test_two_step_confirmation_and_actor(self):
        pending = control.prepare(server, {"action": "worker", "worker": "haxlab-1", "op": "pause"})
        self.assertEqual(pending["risk"], "high")
        self.assertFalse([c for c in self.z.calls if c[0] == "/api/runner-control"])   # nothing happened yet
        result = control.confirm(server, pending["pending_id"])
        self.assertTrue(result["ok"])
        call = next(c for c in self.z.calls if c[0] == "/api/runner-control")
        self.assertEqual(call[1], {"project_id": "haxlab-1", "action": "pause"})
        self.assertEqual(call[2], "virtual-office")
        with self.assertRaisesRegex(ValueError, "verlopen"):
            control.confirm(server, pending["pending_id"])   # single use
        self.assertEqual(control.recent_log(server)[0]["action"], "worker")

    def test_validation_blocks_dangerous_or_unknown(self):
        for bad in ({"action": "priority", "project": "ftmo", "priority": "turbo"},
                    {"action": "worker", "worker": "nope", "op": "pause"},
                    {"action": "worker", "worker": "ftmo-1", "op": "rm"},
                    {"action": "worker_count", "project": "ftmo", "count": 99},
                    {"action": "queue_drop", "queue_id": "unknown"},
                    {"action": "queue_add", "project": "ftmo", "title": "kort"},
                    {"action": "format_disk"}):
            with self.assertRaises(ValueError, msg=bad):
                control.prepare(server, bad)

    def test_actions_map_to_dashboard_endpoints(self):
        cases = [({"action": "priority", "project": "ftmo", "priority": "background"}, "/api/resource-priority", {"project": "ftmo", "priority": "background"}),
                 ({"action": "worker_count", "project": "ftmo", "count": 2}, "/api/runner-workers", {"project_id": "ftmo", "worker_count": 2}),
                 ({"action": "force_push"}, "/api/dynamic-workers/force-push", {}),
                 ({"action": "queue_drop", "queue_id": "ftmo-bbb", "reason": "dubbel"}, "/api/portfolio-queue", None)]
        for body, path, expected in cases:
            self.z.calls.clear()
            control.confirm(server, control.prepare(server, body)["pending_id"])
            call = next(c for c in self.z.calls if c[1] is not None)
            self.assertEqual(call[0], path)
            if expected is not None:
                self.assertEqual(call[1], expected)
        self.assertEqual(call[1]["action"], "drop")
        self.z.calls.clear()
        control.confirm(server, control.prepare(server, {"action": "queue_add", "project": "ftmo", "title": "Voeg een regressietest toe", "queue_priority": "p0"})["pending_id"])
        add = next(c for c in self.z.calls if c[1] is not None)[1]
        self.assertEqual((add["action"], add["priority"]), ("enqueue", "P1"))   # P0 stays reserved for the owner
        self.assertTrue(add["queue_id"].startswith("office-ftmo-"))

    def test_zcloud_refusal_is_reported_and_logged(self):
        self.z.fail = "Project is human-gated"
        pending = control.prepare(server, {"action": "worker", "worker": "ftmo-1", "op": "start"})
        with self.assertRaisesRegex(ValueError, "human-gated"):
            control.confirm(server, pending["pending_id"])
        self.assertEqual(control.recent_log(server)[0]["ok"], 0)

    def test_dashboard_changes_become_events(self):
        self.assertEqual(control.sync_changes(server), [])           # first look only records
        self.z.targets["projects"]["haxlab-1"]["desired_state"] = "paused"   # someone pauses it in the dashboard
        control.reset_cache()
        changes = control.sync_changes(server)
        self.assertEqual(changes, ["Worker haxlab-1: running → paused"])
        event = game.list_events(server)["events"][-1]
        self.assertEqual((event["kind"], event["payload"]["source"]), ("control_changed", "dashboard"))
        control.reset_cache()
        self.assertEqual(control.sync_changes(server), [])           # unchanged: no duplicate

    # ---- experts
    def online(self, employee="maya", client="c1"):
        features.relay_poll(server, {"employee": employee, "client_id": client, "conversation": THREAD, "ready": False})

    def claim(self, employee="maya", client="c1"):
        return features.relay_poll(server, {"employee": employee, "client_id": client, "conversation": THREAD, "ready": True})["job"]

    def reply(self, job, doc, client="c1"):
        text = doc if isinstance(doc, str) else "```json\n" + json.dumps(doc) + "\n```"
        features.relay_reply(server, {"id": job["id"], "client_id": client, "claim_token": job["claim_token"], "reply": text})

    def test_review_roundtrip_creates_validated_proposals_only(self):
        with self.assertRaisesRegex(ValueError, "Relay"):
            experts.request_review(server, {"employee": "maya"})
        self.online()
        experts.request_review(server, {"employee": "maya", "question": "Wat is het belangrijkst?"})
        with self.assertRaisesRegex(ValueError, "al mee"):
            experts.request_review(server, {"employee": "maya"})
        job = self.claim()
        self.assertIn("haxlab-aaa", job["instruction"])      # the real queue is in the prompt
        self.assertIn("Wat is het belangrijkst?", job["instruction"])
        self.reply(job, {"summary": "FTMO staat gepauzeerd terwijl er werk klaarstaat. Ik stel voor om die te starten.", "notion": "Niet geraadpleegd",
                         "proposals": [{"action": "worker", "worker": "ftmo-1", "op": "start", "why": "Er staat een P1-taak klaar"},
                                       {"action": "worker", "worker": "verzonnen-9", "op": "start", "why": "bestaat niet"},
                                       {"action": "priority", "project": "ftmo", "priority": "turbo", "why": "te zwaar"},
                                       {"action": "queue_add", "project": "cloud", "title": "Ruim dubbele taken op", "queue_priority": "P2", "criteria": "klaar als dubbele weg zijn", "why": "hygiëne"}]})
        pending = experts.list_proposals(server)["proposals"]
        self.assertEqual(len(pending), 2)
        self.assertEqual({p["employee"] for p in pending}, {"maya"})
        event = next(e for e in game.list_events(server)["events"] if e["kind"] == "review_ready")
        self.assertEqual((event["payload"]["proposals"], event["payload"]["skipped"]), (2, 2))
        self.assertIn("FTMO staat gepauzeerd", event["payload"]["spoken"])
        self.z.calls.clear()
        self.assertFalse([c for c in self.z.calls if c[1] is not None])      # nothing executed without approval

    def test_approve_executes_and_reject_does_not(self):
        self.online()
        experts.request_review(server, {"employee": "maya"})
        self.reply(self.claim(), {"summary": "Twee voorstellen.", "proposals": [
            {"action": "worker", "worker": "ftmo-1", "op": "start", "why": "a"},
            {"action": "queue_drop", "queue_id": "ftmo-bbb", "why": "b"}]})
        first, second = experts.list_proposals(server)["proposals"]
        self.z.calls.clear()
        self.assertEqual(experts.decide(server, first["id"], True)["status"], "applied")
        self.assertEqual(next(c for c in self.z.calls if c[1] is not None)[0], "/api/runner-control")
        self.z.calls.clear()
        self.assertEqual(experts.decide(server, second["id"], False)["status"], "rejected")
        self.assertFalse([c for c in self.z.calls if c[1] is not None])
        with self.assertRaisesRegex(ValueError, "al behandeld"):
            experts.decide(server, first["id"], True)
        kinds = [e["kind"] for e in game.list_events(server)["events"]]
        self.assertIn("proposal_applied", kinds)

    def test_bad_review_reply_is_visible_not_silent(self):
        self.online()
        experts.request_review(server, {"employee": "maya"})
        self.reply(self.claim(), "Natuurlijk! Hier mijn mening zonder JSON.")
        self.assertEqual(experts.list_proposals(server)["proposals"], [])
        self.assertEqual(game.list_events(server)["events"][-1]["kind"], "review_failed")

    def test_auto_review_is_rate_limited(self):
        self.assertIsNone(experts.auto_review(server))            # nobody online
        self.online("noah", "c2")
        first = experts.auto_review(server)
        self.assertEqual(first["status"], "queued")
        self.assertIsNone(experts.auto_review(server))            # one open review at a time
        with patch.object(experts, "REVIEW_MINUTES", 0):
            self.assertIsNone(experts.auto_review(server))

    def test_local_only_guard(self):
        class Stub:
            def __init__(self, addr):
                self.client_address = (addr, 1)
                self.sent = None
            def json_reply(self, code, obj):
                self.sent = code
        outside = Stub("203.0.113.5")
        self.assertFalse(server.Handler.local_only(outside))
        self.assertEqual(outside.sent, 403)
        self.assertTrue(server.Handler.local_only(Stub("127.0.0.1")))


class TeamControlHTTPTests(test_office.HTTPTests):
    def test_endpoints(self):
        token = "test-only-password"
        z = FakeZ()
        control.reset_cache()
        with patch.object(server, "zcloud", z):
            for path in ("/api/staff", "/api/control/state", "/api/proposals"):
                self.assertEqual(self.ask(path)[0], 401)
            code, doc = self.ask("/api/staff", token=token)
            self.assertEqual((code, len(doc["staff"]), doc["max"]), (200, 3, 8))
            code, doc = self.ask("/api/staff", {"id": "ravi", "name": "Ravi", "title": "Data"}, token=token)
            self.assertEqual(code, 201)
            code, doc = self.ask("/api/employees", token=token)
            self.assertIn("ravi", [e["id"] for e in doc["employees"]])
            code, doc = self.ask("/api/control/state?fresh=1", token=token)
            self.assertEqual((code, len(doc["workers"])), (200, 2))
            code, prep = self.ask("/api/control/prepare", {"action": "worker", "worker": "ftmo-1", "op": "start"}, token=token)
            self.assertEqual(code, 200)
            code, done = self.ask("/api/control/confirm", {"pending_id": prep["pending_id"]}, token=token)
            self.assertEqual((code, done["ok"]), (200, True))
            code, err = self.ask("/api/control/prepare", {"action": "priority", "project": "ftmo", "priority": "turbo"}, token=token)
            self.assertEqual(code, 400)
            self.assertEqual(self.ask("/api/proposals/none/approve", {}, token=token)[0], 400)
            self.assertEqual(self.ask("/api/proposals?status=zzz", token=token)[0], 400)
            self.assertEqual(self.ask("/api/experts/review", {"employee": "maya"}, token=token)[0], 400)   # relay offline


if __name__ == "__main__":
    unittest.main()
