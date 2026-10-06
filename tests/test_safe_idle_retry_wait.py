import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_safe_idle_retry_wait as retry_wait


class SafeIdleRetryWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-safe-idle-retry-")
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE ai_global_slots("
                "slot INTEGER PRIMARY KEY, project_id TEXT, worker_slot INTEGER)"
            )
            conn.execute(
                "CREATE TABLE runner_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, "
                "worker_slot INTEGER, event TEXT, generating INTEGER, sending INTEGER)"
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_worker_key(self):
        self.assertEqual(("cloud", 1), retry_wait.parse_worker_key("cloud::w1"))
        with self.assertRaises(retry_wait.RetryWaitError):
            retry_wait.parse_worker_key("cloud")

    def test_unallocated_blocker_is_resolved(self):
        with retry_wait.connect_read_only(self.db) as conn:
            state = retry_wait.blocker_state(conn, "cloud::w1")
        self.assertTrue(state["ready"])
        self.assertEqual("no-longer-allocated", state["reason"])

    def test_allocated_generating_blocker_is_not_ready(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot) VALUES(1,'cloud',1)"
            )
            conn.execute(
                "INSERT INTO runner_events(project_id,worker_slot,event,generating,sending) "
                "VALUES('cloud',1,'heartbeat',1,0)"
            )
            conn.commit()
        with retry_wait.connect_read_only(self.db) as conn:
            state = retry_wait.blocker_state(conn, "cloud::w1")
        self.assertFalse(state["ready"])
        self.assertEqual("active", state["reason"])
        self.assertTrue(state["generating"])

    def test_allocated_idle_blocker_is_ready(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot) VALUES(1,'cloud',1)"
            )
            conn.execute(
                "INSERT INTO runner_events(project_id,worker_slot,event,generating,sending) "
                "VALUES('cloud',1,'heartbeat',0,0)"
            )
            conn.commit()
        with retry_wait.connect_read_only(self.db) as conn:
            state = retry_wait.blocker_state(conn, "cloud::w1")
        self.assertTrue(state["ready"])
        self.assertEqual("idle", state["reason"])

    def test_reader_opens_sqlite_in_read_only_mode(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "zcloud_safe_idle_retry_wait.py"
        ).read_text(encoding="utf-8")
        self.assertIn('mode=ro', source)
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "REPLACE INTO", "DROP TABLE", "ALTER TABLE"):
            self.assertNotIn(forbidden, source.upper())


if __name__ == "__main__":
    unittest.main(verbosity=2)
