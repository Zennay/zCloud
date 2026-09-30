import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
import unittest

from scripts import zcloud_deploy_safe_idle as safe_idle


class DeploySafeIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.state = self.root / "safe-idle.json"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE ai_global_slots(
                    slot INTEGER PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    worker_slot INTEGER NOT NULL,
                    assigned_at TEXT
                );
                CREATE TABLE runner_workers(
                    project_id TEXT NOT NULL,
                    worker_slot INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    desired_state TEXT NOT NULL DEFAULT 'running',
                    PRIMARY KEY(project_id, worker_slot)
                );
                INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at)
                VALUES(1,'cloud',1,'now');
                INSERT INTO runner_workers(project_id,worker_slot,conversation_id,desired_state)
                VALUES('cloud',1,'conversation-a','running');
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def state_of(self, project_id="cloud", worker_slot=1):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?",
                (project_id, worker_slot),
            ).fetchone()[0]

    def test_enter_waits_for_drain_ack_and_restore_returns_previous_state(self):
        def acknowledge():
            deadline = time.time() + 1
            while time.time() < deadline:
                if self.state_of() == "draining":
                    with sqlite3.connect(self.db) as conn:
                        conn.execute(
                            "UPDATE runner_workers SET desired_state='paused' "
                            "WHERE project_id='cloud' AND worker_slot=1"
                        )
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=acknowledge)
        thread.start()
        result = safe_idle.enter_safe_idle(
            self.db,
            self.state,
            timeout_seconds=1,
            poll_seconds=0.01,
            stable_seconds=0.02,
        )
        thread.join(timeout=1)

        self.assertTrue(result["safe_idle"])
        self.assertEqual("paused", self.state_of())
        payload = json.loads(self.state.read_text())
        self.assertEqual("running", payload["workers"][0]["previous_state"])

        restored = safe_idle.restore_safe_idle(self.db, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual("running", self.state_of())

    def test_timeout_restores_previous_state_fail_closed(self):
        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
            )
        self.assertEqual("running", self.state_of())
        payload = json.loads(self.state.read_text())
        self.assertTrue(payload["restored_after_timeout"])


if __name__ == "__main__":
    unittest.main()
