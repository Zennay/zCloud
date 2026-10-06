import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_worker_state_machine as machine


class WorkerStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-worker-state-")
        self.db = Path(self.tmp.name) / "history.db"
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executescript(
                """
                CREATE TABLE runner_workers(
                    project_id TEXT NOT NULL,
                    worker_slot INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    desired_state TEXT NOT NULL DEFAULT 'running',
                    provider TEXT NOT NULL DEFAULT 'chatgpt',
                    PRIMARY KEY(project_id,worker_slot)
                );
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    target TEXT,
                    title TEXT,
                    generating INTEGER,
                    sending INTEGER,
                    reason TEXT,
                    tab_id INTEGER,
                    error TEXT,
                    project_id TEXT,
                    progress_at TEXT,
                    assistant_chars INTEGER,
                    worker_slot INTEGER NOT NULL DEFAULT 1
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
                """
            )
            connection.execute("INSERT INTO runner_workers(project_id,worker_slot,desired_state) VALUES('cloud',1,'running')")
            connection.commit()
        self.now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def set_desired(self, value):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE runner_workers SET desired_state=? WHERE project_id='cloud' AND worker_slot=1", (value,))
            connection.commit()

    def event(self, event, *, seconds=10, generating=0, sending=0, secret=""):
        ts = (self.now - timedelta(seconds=seconds)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_events(
                       ts,event,target,title,generating,sending,reason,error,project_id,worker_slot
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ts,event,f"target-{secret}",f"title-{secret}",generating,sending,f"reason-{secret}",f"error-{secret}","cloud",1),
            )
            connection.commit()

    def command(self, status, *, seconds=5, action="push", secret=""):
        ts = (self.now - timedelta(seconds=seconds)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                """INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result)
                   VALUES(?,?,?,?,?,?)""",
                ("cloud::w1",action,status,ts,ts,f"result-{secret}"),
            )
            connection.commit()

    def state(self):
        payload = machine.report(self.db, project="cloud", worker_slot=1, now=self.now)
        self.assertEqual(1, payload["worker_count"])
        return payload["workers"][0]

    def test_paused_and_draining_are_durable_state_authority(self):
        self.event("heartbeat", generating=1)
        self.command("failed")
        self.set_desired("paused")
        self.assertEqual("paused", self.state()["state"])
        self.set_desired("draining")
        self.assertEqual("draining", self.state()["state"])

    def test_blocked_survives_heartbeat_until_semantic_recovery(self):
        self.event("send-blocked", seconds=20)
        self.event("heartbeat", seconds=10, generating=0)
        self.assertEqual("blocked", self.state()["state"])
        self.event("prompt-sent", seconds=5)
        self.assertEqual("waiting", self.state()["state"])

    def test_fresh_active_heartbeat_is_running(self):
        self.event("heartbeat", seconds=5, generating=1)
        item = self.state()
        self.assertEqual("running", item["state"])
        self.assertEqual("fresh_heartbeat_active", item["reason_code"])

    def test_stale_or_idle_heartbeat_is_waiting(self):
        self.event("heartbeat", seconds=600, generating=1)
        stale = self.state()
        self.assertEqual("waiting", stale["state"])
        self.assertEqual("stale", stale["heartbeat_freshness"])

        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("DELETE FROM runner_events")
            connection.commit()
        self.event("heartbeat", seconds=5, generating=0, sending=0)
        self.assertEqual("waiting", self.state()["state"])

    def test_newer_failed_worker_control_is_failed_but_recovery_supersedes_it(self):
        self.event("generation-finished", seconds=30)
        self.command("failed", seconds=20)
        self.assertEqual("failed", self.state()["state"])
        self.event("prompt-sent", seconds=10)
        self.assertEqual("waiting", self.state()["state"])

    def test_project_wide_or_other_worker_failure_is_not_misattributed(self):
        ts = (self.now - timedelta(seconds=5)).isoformat()
        with closing(sqlite3.connect(self.db)) as connection:
            for project_id in ("cloud", "cloud::w2"):
                connection.execute(
                    "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)",
                    (project_id,"push","failed",ts,ts,"sensitive"),
                )
            connection.commit()
        self.assertEqual("waiting", self.state()["state"])

    def test_raw_payloads_are_never_exposed_and_database_is_immutable(self):
        secret = "STATE_MACHINE_SECRET_919"
        self.event("send-blocked", secret=secret)
        self.command("completed", secret=secret)
        before_bytes = self.db.read_bytes()
        before_mtime = self.db.stat().st_mtime_ns
        payload = machine.report(self.db, project="cloud", now=self.now)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret, serialized)
        for forbidden in ("target", "title", "reason", "error", "result", "conversation_id"):
            self.assertNotIn(f'"{forbidden}"', serialized)
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_mtime, self.db.stat().st_mtime_ns)

    def test_rejects_invalid_desired_state_and_symlink(self):
        self.set_desired("mystery")
        with self.assertRaisesRegex(ValueError, "invalid_desired_state"):
            self.state()
        link = Path(self.tmp.name) / "history-link.db"
        try:
            os.symlink(self.db, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unsupported")
        with self.assertRaisesRegex(ValueError, "database_symlink_not_allowed"):
            machine.report(link, project="cloud", now=self.now)

    def test_requires_project_when_filtering_worker_slot(self):
        with self.assertRaisesRegex(ValueError, "worker_slot_requires_project"):
            machine.report(self.db, worker_slot=1, now=self.now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
