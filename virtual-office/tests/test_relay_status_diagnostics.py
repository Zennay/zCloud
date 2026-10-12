"""Regression checks for Office Relay heartbeat status and conversation routing."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class RelayStatusDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(server, "DB", Path(self.temp.name) / "office.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        server.init_db()

    def test_initial_status_does_not_claim_connection(self):
        result = server.features.relay_status(server)["employees"]
        self.assertEqual(set(result), {"maya", "noah", "lina"})
        for item in result.values():
            self.assertFalse(item["online"])
            self.assertIsNone(item["last_seen_seconds"])
            self.assertIsNone(item["conversation"])

    def test_only_recent_claiming_tab_is_linked(self):
        url = "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"
        server.features.relay_poll(server, {
            "employee": "maya", "client_id": "local-test-client",
            "conversation": url, "ready": False,
        })
        live = server.features.relay_status(server)["employees"]
        self.assertTrue(live["maya"]["online"])
        self.assertEqual(live["maya"]["conversation"], url)
        self.assertLess(live["maya"]["last_seen_seconds"], 35)
        self.assertFalse(live["noah"]["online"])
        with server.connect() as db:
            db.execute("UPDATE office_relay SET heartbeat=? WHERE employee='maya'", (time.time() - 90,))
        stale = server.features.relay_status(server)["employees"]["maya"]
        self.assertFalse(stale["online"])
        self.assertGreaterEqual(stale["last_seen_seconds"], 35)
        self.assertIsNone(stale["conversation"])

    def test_health_does_not_disclose_claim_or_access_token(self):
        url = "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"
        server.features.relay_poll(server, {
            "employee": "lina", "client_id": "client-private",
            "conversation": url, "ready": False,
        })
        status = server.features.relay_status(server)
        self.assertNotIn("client-private", str(status))
        self.assertNotIn("claim_token", str(status))


if __name__ == "__main__":
    unittest.main()
