import json
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
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
                CREATE TABLE runner_commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result TEXT
                );
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,
                    project_id TEXT,
                    worker_slot INTEGER NOT NULL DEFAULT 1,
                    generating INTEGER NOT NULL DEFAULT 0,
                    sending INTEGER NOT NULL DEFAULT 0
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

    def pending_drain(self, worker_id="cloud::w1"):
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT id,status FROM runner_commands "
                "WHERE project_id=? AND action='drain' ORDER BY id DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
        return row

    def test_enter_waits_for_drain_ack_and_restore_returns_previous_state(self):
        def acknowledge():
            deadline = time.time() + 1
            while time.time() < deadline:
                command = self.pending_drain()
                if self.state_of() == "draining" and command is not None:
                    with sqlite3.connect(self.db) as conn:
                        conn.execute(
                            "UPDATE runner_workers SET desired_state='paused' "
                            "WHERE project_id='cloud' AND worker_slot=1"
                        )
                        conn.execute(
                            "UPDATE runner_commands SET status='completed',result='runner drained' "
                            "WHERE id=?",
                            (command[0],),
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
        self.assertEqual(1, len(payload["drain_command_ids"]))
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT project_id,action,status FROM runner_commands WHERE id=?",
                (payload["drain_command_ids"][0],),
            ).fetchone()
        self.assertEqual(("cloud::w1", "drain", "completed"), row)

        restored = safe_idle.restore_safe_idle(self.db, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual("running", self.state_of())

    def test_enter_supersedes_pending_push_before_drain(self):
        created = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db) as conn:
            push_id = conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) "
                "VALUES(?,?,?,?,?,NULL)",
                ("cloud::w1", "push", "pending", created, created),
            ).lastrowid
        self.add_event(age_seconds=600, generating=0, sending=0)

        result = safe_idle.enter_safe_idle(
            self.db,
            self.state,
            timeout_seconds=0.5,
            poll_seconds=0.01,
            stable_seconds=0.02,
            offline_after_seconds=300,
        )

        self.assertTrue(result["safe_idle"])
        self.assertIn(push_id, result["superseded_push_command_ids"])
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?",
                (push_id,),
            ).fetchone()
        self.assertEqual("failed", row[0])
        self.assertIn("superseded pre-deploy pending push", row[1])

        restored = safe_idle.restore_safe_idle(self.db, self.state)
        self.assertTrue(restored["ok"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                "failed",
                conn.execute(
                    "SELECT status FROM runner_commands WHERE id=?",
                    (push_id,),
                ).fetchone()[0],
            )

    def test_missing_allocated_worker_state_fails_closed_without_creating_mapping(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "DELETE FROM runner_workers WHERE project_id='cloud' AND worker_slot=1"
            )

        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
            )

        with sqlite3.connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM runner_workers WHERE project_id='cloud' AND worker_slot=1"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def add_event(self, *, age_seconds, generating=0, sending=0, event="heartbeat"):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                "VALUES(?,?,?,?,?,?)",
                (ts, event, "cloud", 1, generating, sending),
            )

    def test_offline_idle_worker_is_auto_quiesced_and_restored(self):
        self.add_event(age_seconds=600, generating=0, sending=0)
        result = safe_idle.enter_safe_idle(
            self.db,
            self.state,
            timeout_seconds=0.5,
            poll_seconds=0.01,
            stable_seconds=0.02,
            offline_after_seconds=300,
        )
        self.assertTrue(result["safe_idle"])
        self.assertEqual("paused", self.state_of())
        payload = json.loads(self.state.read_text())
        self.assertEqual("cloud::w1", payload["auto_quiesced_workers"][0]["worker_id"])
        command = self.pending_drain()
        self.assertEqual("completed", command[1])
        restored = safe_idle.restore_safe_idle(self.db, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual("running", self.state_of())

    def test_offline_generating_worker_stays_fail_closed(self):
        self.add_event(age_seconds=600, generating=1, sending=0, event="generation-started")
        with self.assertRaisesRegex(safe_idle.SafeIdleError, "cloud::w1"):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
                offline_after_seconds=300,
            )
        payload = json.loads(self.state.read_text())
        blocker = payload["blocking_workers"][0]
        self.assertEqual("cloud::w1", blocker["worker_id"])
        self.assertTrue(blocker["generating"])
        self.assertEqual("running", self.state_of())

    def test_recent_idle_worker_still_requires_browser_drain_ack(self):
        self.add_event(age_seconds=1, generating=0, sending=0)
        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
                offline_after_seconds=300,
            )
        payload = json.loads(self.state.read_text())
        self.assertEqual("pending", payload["blocking_workers"][0]["drain_command_status"])
        self.assertEqual("running", self.state_of())

    def test_post_drain_config_ack_auto_quiesces_idle_worker(self):
        def acknowledge_config():
            deadline = time.time() + 1
            while time.time() < deadline:
                command = self.pending_drain()
                if self.state_of() == "draining" and command is not None:
                    with sqlite3.connect(self.db) as conn:
                        conn.execute(
                            "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                            "VALUES(?,?,?,?,?,?)",
                            (
                                datetime.now(timezone.utc).isoformat(),
                                "runner-config-updated",
                                "cloud",
                                1,
                                0,
                                0,
                            ),
                        )
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=acknowledge_config)
        thread.start()
        result = safe_idle.enter_safe_idle(
            self.db,
            self.state,
            timeout_seconds=1,
            poll_seconds=0.01,
            stable_seconds=0.02,
            offline_after_seconds=300,
        )
        thread.join(timeout=1)

        self.assertTrue(result["safe_idle"])
        self.assertEqual("paused", self.state_of())
        payload = json.loads(self.state.read_text())
        self.assertEqual(
            "acknowledged-idle",
            payload["auto_quiesced_workers"][0]["reason"],
        )
        command = self.pending_drain()
        self.assertEqual("completed", command[1])

        restored = safe_idle.restore_safe_idle(self.db, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual("running", self.state_of())

    def test_failed_drain_with_post_command_runner_stop_is_terminal_idle(self):
        def stop_after_drain():
            deadline = time.time() + 1
            while time.time() < deadline:
                command = self.pending_drain()
                if self.state_of() == "draining" and command is not None:
                    with sqlite3.connect(self.db) as conn:
                        conn.execute(
                            "UPDATE runner_commands SET status='failed',result=? WHERE id=?",
                            ("worker-progress-watchdog superseded stale pending command", command[0]),
                        )
                        conn.execute(
                            "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                            "VALUES(?,?,?,?,?,?)",
                            (
                                datetime.now(timezone.utc).isoformat(),
                                "runner-stopped",
                                "cloud",
                                1,
                                0,
                                0,
                            ),
                        )
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=stop_after_drain)
        thread.start()
        result = safe_idle.enter_safe_idle(
            self.db,
            self.state,
            timeout_seconds=1,
            poll_seconds=0.01,
            stable_seconds=0.02,
            offline_after_seconds=300,
        )
        thread.join(timeout=1)

        self.assertTrue(result["safe_idle"])
        self.assertEqual("paused", self.state_of())
        payload = json.loads(self.state.read_text())
        self.assertEqual("terminal-idle", payload["auto_quiesced_workers"][0]["reason"])
        command = self.pending_drain()
        self.assertEqual("failed", command[1])

    def test_pre_drain_runner_stop_does_not_count_as_terminal_idle(self):
        self.add_event(
            age_seconds=1,
            generating=0,
            sending=0,
            event="runner-stopped",
        )
        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
                offline_after_seconds=300,
            )
        payload = json.loads(self.state.read_text())
        self.assertEqual("pending", payload["blocking_workers"][0]["drain_command_status"])
        self.assertEqual("running", self.state_of())

    def test_pre_drain_config_event_does_not_count_as_ack(self):
        self.add_event(
            age_seconds=1,
            generating=0,
            sending=0,
            event="runner-config-updated",
        )
        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                stable_seconds=0.01,
                offline_after_seconds=300,
            )
        payload = json.loads(self.state.read_text())
        self.assertEqual("pending", payload["blocking_workers"][0]["drain_command_status"])
        self.assertEqual("running", self.state_of())

    def test_post_drain_ack_never_auto_quiesces_generating_worker(self):
        def acknowledge_config():
            deadline = time.time() + 1
            while time.time() < deadline:
                command = self.pending_drain()
                if self.state_of() == "draining" and command is not None:
                    with sqlite3.connect(self.db) as conn:
                        conn.execute(
                            "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                            "VALUES(?,?,?,?,?,?)",
                            (
                                datetime.now(timezone.utc).isoformat(),
                                "runner-config-updated",
                                "cloud",
                                1,
                                1,
                                0,
                            ),
                        )
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=acknowledge_config)
        thread.start()
        with self.assertRaises(safe_idle.SafeIdleError):
            safe_idle.enter_safe_idle(
                self.db,
                self.state,
                timeout_seconds=0.1,
                poll_seconds=0.01,
                stable_seconds=0.01,
                offline_after_seconds=300,
            )
        thread.join(timeout=1)
        payload = json.loads(self.state.read_text())
        self.assertTrue(payload["blocking_workers"][0]["generating"])
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
        self.assertEqual(1, len(payload["drain_command_ids"]))
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?",
                (payload["drain_command_ids"][0],),
            ).fetchone()
        self.assertEqual("failed", row[0])
        self.assertIn("safe-idle ended", row[1])


if __name__ == "__main__":
    unittest.main()
