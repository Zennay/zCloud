"""Game-facing Office features: events, grounded summaries and employee-made decks. Offline and isolated."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import office_features as features
import office_game as game
import server
import test_office

THREAD = "https://chatgpt.com/c/0123456789abcdef0123"
EVIDENCE = "PR #12 is gemerged en alle 48 tests slagen. De nieuwe loginpagina staat live."


def relay_online(employee="maya", client="c1"):
    return features.relay_poll(server, {"employee": employee, "client_id": client, "conversation": THREAD, "ready": False})


def finish_remote(job, status="done", evidence=EVIDENCE, blocker=""):
    remote_id = server.office_queue_id(job)
    with patch.object(server, "zcloud", return_value={"item": {"queue_id": remote_id, "status": "queued"}}):
        server.dispatch_once()
    item = {"queue_id": remote_id, "status": status, "evidence": evidence, "blocker": blocker}
    with patch.object(server, "zcloud", return_value={"items": [item]}):
        server.sync_remote()


class GameFeatureTests(test_office.OfficeTests):
    def poll_claim(self, employee="maya", client="c1"):
        return features.relay_poll(server, {"employee": employee, "client_id": client, "conversation": THREAD, "ready": True})["job"]

    def test_done_job_creates_one_event_with_evidence_summary(self):
        job = server.enqueue("noah", "task", "Bouw de loginpagina")
        finish_remote(job)
        finish_remote(job)  # replaying sync must not duplicate the event
        events = game.list_events(server)["events"]
        self.assertEqual([(e["employee"], e["kind"], e["ref"]) for e in events], [("noah", "job_done", job["id"])])
        summary = game.summary_for(server, job["id"])
        self.assertEqual(summary["source"], "evidence")
        self.assertIn("48 tests", summary["text"])
        self.assertFalse(summary["pending"])

    def test_summary_never_invents_results_without_evidence(self):
        job = server.enqueue("maya", "task", "Doe iets groots")
        finish_remote(job, evidence="")
        text = game.summary_for(server, job["id"])["text"]
        self.assertIn("geen toelichting", text)
        self.assertNotIn("Doe iets groots", text)

    def test_dropped_job_reports_attention(self):
        job = server.enqueue("lina", "task", "Test LightUp")
        finish_remote(job, status="dropped", evidence="", blocker="Bron geblokkeerd")
        self.assertEqual(game.list_events(server)["events"][0]["kind"], "job_failed")
        self.assertIn("aandacht", game.summary_for(server, job["id"])["text"])

    def test_relay_phrases_summary_from_evidence_only(self):
        job = server.enqueue("maya", "task", "Zet de build groen")
        finish_remote(job)
        game.queue_summaries(server)
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM office_chat").fetchone()[0], 0)  # relay offline: no request
        relay_online()
        game.queue_summaries(server)
        game.queue_summaries(server)  # idempotent
        claimed = self.poll_claim()
        self.assertIn(EVIDENCE[:30], claimed["instruction"])
        self.assertIn("maximaal 2 korte zinnen", claimed["instruction"])
        self.assertTrue(game.summary_for(server, job["id"])["pending"])
        features.relay_reply(server, {"id": claimed["id"], "client_id": "c1", "claim_token": claimed["claim_token"],
                                      "reply": "**Klaar!** De build is groen en de loginpagina staat live. Verder niets."})
        summary = game.summary_for(server, job["id"])
        self.assertEqual(summary["source"], "chatgpt")
        self.assertNotIn("*", summary["text"])
        self.assertLessEqual(summary["text"].count("."), 2)
        kinds = [e["kind"] for e in game.list_events(server)["events"]]
        self.assertEqual(kinds, ["job_done", "summary_ready"])
        with server.connect() as db:  # internal prompt/reply must not pollute the visible chat
            roles = [r["role"] for r in db.execute("SELECT role FROM messages")]
        self.assertNotIn("assistant", roles)

    def test_failed_relay_reply_falls_back_to_evidence(self):
        job = server.enqueue("maya", "task", "Zet de build groen")
        finish_remote(job)
        relay_online()
        game.queue_summaries(server)
        claimed = self.poll_claim()
        features.relay_reply(server, {"id": claimed["id"], "client_id": "c1", "claim_token": claimed["claim_token"],
                                      "status": "attention", "reply": "Inloggen nodig"})
        summary = game.summary_for(server, job["id"])
        self.assertEqual(summary["source"], "evidence")
        self.assertFalse(summary["pending"])

    def test_deck_request_requires_online_relay(self):
        with self.assertRaisesRegex(ValueError, "Relay"):
            game.request_deck(server, {"employee": "lina", "topic": "Securitystatus"})
        with self.assertRaises(ValueError):
            game.request_deck(server, {"employee": "nobody", "topic": "Iets"})

    def test_employee_deck_roundtrip(self):
        relay_online("lina")
        ack = game.request_deck(server, {"employee": "lina", "topic": "Securitystatus van LightUp"})
        self.assertEqual(ack["status"], "queued")
        with self.assertRaisesRegex(ValueError, "al een presentatie"):
            game.request_deck(server, {"employee": "lina", "topic": "Nog een"})
        claimed = self.poll_claim("lina")
        self.assertIn("Securitystatus van LightUp", claimed["instruction"])
        reply = "```json\n" + json.dumps({"title": "LightUp status", "slides": [
            {"title": "Waar staan we", "body": "M1 loopt\nTests groen", "layout": "bullets", "accent": "teal",
             "speaker": "Dit is de stand van zaken."},
            {"title": "Testen", "body": "", "layout": "stat", "stat": {"value": "48", "label": "tests geslaagd"},
             "visual": "bars", "data": [{"label": "geslaagd", "value": 48}, {"label": "mislukt", "value": 0}]},
            {"title": "Onveilig", "body": "x", "layout": "<script>", "accent": "javascript:alert(1)",
             "data": [{"label": "a", "value": "NaN"}]}]}) + "\n```"
        features.relay_reply(server, {"id": claimed["id"], "client_id": "c1", "claim_token": claimed["claim_token"], "reply": reply})
        events = game.list_events(server, employee="lina")["events"]
        self.assertEqual(events[-1]["kind"], "deck_ready")
        deck = features.get_deck(server, events[-1]["ref"])
        self.assertEqual(deck["title"], "LightUp status")
        self.assertEqual(deck["slides"][0]["speaker"], "Dit is de stand van zaken.")
        self.assertEqual(deck["slides"][1]["data"][0], {"label": "geslaagd", "value": 48})
        self.assertEqual(deck["slides"][1]["stat"]["value"], "48")
        self.assertNotIn("layout", deck["slides"][2])
        self.assertNotIn("accent", deck["slides"][2])
        self.assertNotIn("data", deck["slides"][2])

    def test_invalid_deck_reply_is_rejected_visibly(self):
        relay_online("noah", "c2")
        game.request_deck(server, {"employee": "noah", "topic": "Supa roadmap"})
        claimed = self.poll_claim("noah", "c2")
        features.relay_reply(server, {"id": claimed["id"], "client_id": "c2", "claim_token": claimed["claim_token"],
                                      "reply": "Natuurlijk! Hier is je presentatie over Supa."})
        events = game.list_events(server)["events"]
        self.assertEqual(events[-1]["kind"], "deck_failed")
        self.assertEqual(features.deck_list(server)["decks"], [])
        with server.connect() as db:
            last = db.execute("SELECT content FROM messages WHERE role='system' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertIn("kon niet worden gebruikt", last)

    def test_event_cursor_and_validation(self):
        job = server.enqueue("maya", "task", "Taak")
        finish_remote(job)
        last = game.list_events(server)["last_id"]
        self.assertEqual(game.list_events(server, last)["events"], [])
        with self.assertRaises(ValueError):
            game.list_events(server, "abc")
        with self.assertRaises(ValueError):
            game.list_events(server, 0, "nobody")

    def test_parse_deck_reply_limits(self):
        many = json.dumps({"slides": [{"title": "t%d" % i, "body": ""} for i in range(30)]})
        self.assertEqual(len(game.parse_deck_reply(many)["slides"]), 20)
        for bad in ("", "{", '{"slides": []}', '{"slides": "x"}', "[1,2]"):
            with self.assertRaises(ValueError):
                game.parse_deck_reply(bad)


class GameHTTPTests(test_office.HTTPTests):
    def test_endpoints_require_auth_and_work(self):
        token = "test-only-password"
        for path in ("/api/events", "/api/jobs/abc/summary"):
            self.assertEqual(self.ask(path)[0], 401)
        self.assertEqual(self.ask("/api/decks/request", {"employee": "maya", "topic": "x y z"})[0], 401)
        job = server.enqueue("maya", "task", "Bouw de loginpagina")
        finish_remote(job)
        code, doc = self.ask("/api/events?since=0", token=token)
        self.assertEqual((code, doc["events"][0]["kind"]), (200, "job_done"))
        code, summary = self.ask("/api/jobs/" + job["id"] + "/summary", token=token)
        self.assertEqual(code, 200)
        self.assertIn("48 tests", summary["text"])
        self.assertEqual(self.ask("/api/jobs/doesnotexist/summary", token=token)[0], 404)
        self.assertEqual(self.ask("/api/events?since=zz", token=token)[0], 400)
        code, err = self.ask("/api/decks/request", {"employee": "maya", "topic": "Een presentatie"}, token=token)
        self.assertEqual(code, 400)
        self.assertIn("Relay", err["error"])


if __name__ == "__main__":
    unittest.main()
