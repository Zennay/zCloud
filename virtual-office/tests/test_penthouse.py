"""Private workstation, durable decks, relay ownership, no duplicate replies."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import office_features as features

class PenthouseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = patch.object(server, "DB", Path(self.tmp.name) / "office.db")
        self.db.start()
        self.addCleanup(self.db.stop)
        server.init_db()
    def relay(self, employee="maya", client="tab-one", ready=False):
        return features.relay_poll(server, {"employee":employee,"client_id":client,
            "conversation":"https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc","ready":ready})
    def test_relay_installation_is_https_and_standalone(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "office-relay.user.js").read_text(encoding="utf-8")
        install = "https://raw.githubusercontent.com/Zennay/zCloud/feature/virtual-ai-office-mvp-20261012/virtual-office/office-relay.user.js"
        self.assertIn('href="' + install + '"', page)
        self.assertNotIn('href="/office-relay.user.js"', page)
        self.assertEqual(script.splitlines()[0], "// ==UserScript==")
        self.assertIn("// ==/UserScript==", script)
        self.assertIn("// @match        https://chatgpt.com/*", script)
        self.assertIn("// @downloadURL  " + install, script)
        self.assertIn("// @updateURL    " + install, script)

    def test_durable_deck_roundtrip(self):
        d=features.save_deck(server,{"title":"Project","slides":[{"title":"Resultaten","body":"Eén\nTwee"}]})
        server.init_db()
        self.assertEqual(features.get_deck(server,d["id"]),d)
        self.assertEqual(len(features.deck_list(server)["decks"]),1)
        features.save_deck(server,{**d,"title":"Bijgewerkt"})
        self.assertEqual(len(features.deck_list(server)["decks"]),1)
    def test_bad_deck_preserves_original(self):
        d=features.save_deck(server,{"title":"Valid","slides":[{"title":"Slide","body":"OK"}]})
        for bad in [[],[{"title":"x","body":"x"*2001}],["not-an-object"]]:
            with self.assertRaises(ValueError):
                features.save_deck(server,{**d,"slides":bad})
        self.assertEqual(features.get_deck(server,d["id"]),d)
    def test_workstation_only_fixed_commands(self):
        with patch.object(features.subprocess,"run") as execute:
            for bad in ["uptime; id","cat /etc/passwd","env","sudo reboot","disk /tmp"]:
                with self.assertRaises(ValueError):
                    features.workstation(server,bad)
            execute.assert_not_called()
        with patch.object(features.subprocess,"run") as execute:
            execute.return_value.stdout="real fixture output"
            execute.return_value.returncode=0
            result=features.workstation(server,"uptime")
            self.assertEqual(result["output"],"real fixture output")
            self.assertEqual(execute.call_args.args[0],["/usr/bin/uptime"])
            self.assertFalse(execute.call_args.kwargs["shell"])
    def test_offline_chat_never_queued_as_work(self):
        with self.assertRaises(ValueError):
            features.chat_enqueue(server,{"employee":"maya","instruction":"Hoe gaat het?"})
        self.assertEqual(server.list_jobs(),[])
    def test_claim_owner_and_idempotent_reply(self):
        self.relay()
        job=features.chat_enqueue(server,{"employee":"maya","instruction":"Vat ons plan samen"})
        claimed=self.relay(ready=True)["job"]
        self.assertEqual(claimed["id"],job["id"])
        self.assertIsNone(self.relay(ready=True)["job"])
        payload={**claimed,"client_id":"tab-one","reply":"Een echt zichtbaar ChatGPT-antwoord"}
        with self.assertRaises(ValueError):
            features.relay_reply(server,{**payload,"client_id":"wrong-tab"})
        features.relay_reply(server,payload)
        self.assertTrue(features.relay_reply(server,payload)["duplicate"])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0],1)
    def test_busy_tab_not_claimed_and_other_tab_rejected(self):
        self.relay()
        features.chat_enqueue(server,{"employee":"maya","instruction":"Vertel de status"})
        self.assertIsNone(self.relay(ready=False)["job"])
        with self.assertRaises(ValueError):
            self.relay(client="other")
        self.assertIsNotNone(self.relay(ready=True)["job"])
    def test_restart_does_not_replay_claimed_prompt(self):
        self.relay()
        features.chat_enqueue(server,{"employee":"maya","instruction":"Een vraag"})
        claim=self.relay(ready=True)["job"]
        server.init_db()
        self.assertIsNone(self.relay(ready=True)["job"])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM office_chat WHERE id=?",(claim["id"],)).fetchone()[0],"attention")
    def test_job_receipt_is_system_status_not_fake_ai(self):
        result=server.enqueue("maya","task","Bouw de kaart")
        self.assertIn("ontvangen",result["acknowledgement"])
        with server.connect() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT role FROM messages ORDER BY id")],["user","system"])
    def test_capacity_is_atomic_under_concurrency(self):
        def enqueue(_):
            try:
                return bool(server.enqueue("maya","task","Bouw een test"))
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted=list(pool.map(enqueue,range(24)))
        self.assertEqual(sum(accepted),server.MAX_ACTIVE)
        self.assertEqual(len(server.list_jobs()),server.MAX_ACTIVE)

if __name__ == "__main__":
    unittest.main()
