import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_cleanup_plan.py"
SPEC = importlib.util.spec_from_file_location("zcloud_cleanup_plan", MODULE_PATH)
cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cleanup)


class SafeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE task_claims(
                    project_id TEXT NOT NULL, claim_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL, worker_id TEXT NOT NULL DEFAULT '',
                    acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(project_id,claim_key)
                );
                CREATE TABLE worker_preflights(
                    project_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL, checked_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, notion_json TEXT NOT NULL,
                    github_json TEXT NOT NULL, claims_fingerprint TEXT NOT NULL,
                    claims_json TEXT NOT NULL, vps_json TEXT NOT NULL,
                    PRIMARY KEY(project_id,worker_id,owner_id)
                );
                CREATE TABLE runner_commands(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, result TEXT
                );
                CREATE TABLE portfolio_queue(queue_id TEXT PRIMARY KEY);
                CREATE TABLE config_audit(id INTEGER PRIMARY KEY);
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, observed):
        old = (observed - timedelta(hours=2)).isoformat()
        fresh = (observed + timedelta(hours=2)).isoformat()
        old_command = (observed - timedelta(days=31)).isoformat()
        fresh_command = (observed - timedelta(days=2)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?)",
                ("cloud","expired","owner","cloud::w1",old,old,old,"{}"),
            )
            conn.execute(
                "INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?)",
                ("cloud","fresh","owner","cloud::w1",old,old,fresh,"{}"),
            )
            conn.execute(
                "INSERT INTO worker_preflights VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("cloud","cloud::w1","owner",old,old,"{}","{}","x","[]","{}"),
            )
            conn.execute(
                "INSERT INTO worker_preflights VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("cloud","cloud::w2","owner",old,fresh,"{}","{}","y","[]","{}"),
            )
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)",
                ("cloud::w1","push","completed",old_command,old_command,"done"),
            )
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)",
                ("cloud::w1","push","pending",old_command,old_command,None),
            )
            conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)",
                ("cloud::w1","push","failed",fresh_command,fresh_command,"recent"),
            )

    def counts(self):
        with sqlite3.connect(self.db) as conn:
            return {
                "task_claims": conn.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0],
                "worker_preflights": conn.execute("SELECT COUNT(*) FROM worker_preflights").fetchone()[0],
                "runner_commands": conn.execute("SELECT COUNT(*) FROM runner_commands").fetchone()[0],
                "portfolio_queue": conn.execute("SELECT COUNT(*) FROM portfolio_queue").fetchone()[0],
                "config_audit": conn.execute("SELECT COUNT(*) FROM config_audit").fetchone()[0],
            }

    def test_dry_run_selects_only_expired_ephemeral_rows_without_mutation(self):
        observed = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
        self.seed(observed)
        before = self.counts()

        result = cleanup.run_cleanup(
            self.db,
            apply=False,
            confirm="",
            grace_hours=1,
            command_retention_days=30,
            batch_limit=250,
            observed_at=observed,
        )

        self.assertEqual("dry-run", result["mode"])
        self.assertEqual(
            {"task_claims": 1, "worker_preflights": 1, "runner_commands": 1},
            result["plan"]["counts"],
        )
        self.assertEqual(before, self.counts())
        self.assertIn("portfolio_queue", result["excluded"])
        self.assertIn("conversation_ids", result["excluded"])
        self.assertIn("config_audit", result["excluded"])

    def test_apply_requires_exact_confirmation_and_preserves_fresh_or_nonterminal_state(self):
        observed = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
        self.seed(observed)
        with self.assertRaisesRegex(cleanup.CleanupError, "requires --confirm"):
            cleanup.run_cleanup(
                self.db,
                apply=True,
                confirm="wrong",
                grace_hours=1,
                command_retention_days=30,
                batch_limit=250,
                observed_at=observed,
            )

        result = cleanup.run_cleanup(
            self.db,
            apply=True,
            confirm=cleanup.CONFIRM_TOKEN,
            grace_hours=1,
            command_retention_days=30,
            batch_limit=250,
            observed_at=observed,
        )

        self.assertEqual(
            {"task_claims": 1, "worker_preflights": 1, "runner_commands": 1},
            result["deleted"],
        )
        self.assertEqual(
            {
                "task_claims": 1,
                "worker_preflights": 1,
                "runner_commands": 2,
                "portfolio_queue": 0,
                "config_audit": 0,
            },
            self.counts(),
        )
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                [("fresh",)],
                conn.execute("SELECT claim_key FROM task_claims").fetchall(),
            )
            statuses = conn.execute(
                "SELECT status FROM runner_commands ORDER BY id"
            ).fetchall()
        self.assertEqual([("pending",), ("failed",)], statuses)

    def test_batch_limit_is_bounded_and_apply_never_reaches_out_of_scope_tables(self):
        observed = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
        old = (observed - timedelta(hours=3)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO portfolio_queue VALUES('keep')")
            conn.execute("INSERT INTO config_audit VALUES(1)")
            for n in range(3):
                conn.execute(
                    "INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?)",
                    ("cloud",f"expired-{n}","owner","cloud::w1",old,old,old,"{}"),
                )

        result = cleanup.run_cleanup(
            self.db,
            apply=True,
            confirm=cleanup.CONFIRM_TOKEN,
            grace_hours=1,
            command_retention_days=30,
            batch_limit=2,
            observed_at=observed,
        )
        self.assertEqual(2, result["deleted"]["task_claims"])
        self.assertEqual(1, self.counts()["task_claims"])
        self.assertEqual(1, self.counts()["portfolio_queue"])
        self.assertEqual(1, self.counts()["config_audit"])
        with self.assertRaisesRegex(cleanup.CleanupError, "between 1 and 1000"):
            cleanup.run_cleanup(
                self.db,
                apply=False,
                confirm="",
                grace_hours=1,
                command_retention_days=30,
                batch_limit=1001,
                observed_at=observed,
            )

    def test_cli_is_dry_run_by_default_and_symlink_db_is_rejected(self):
        observed = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
        self.seed(observed)
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--db", str(self.db)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("dry-run", payload["mode"])

        link = Path(self.tmp.name) / "state-link.db"
        link.symlink_to(self.db)
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--db", str(link)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, proc.returncode)
        self.assertIn("refusing symlink database path", proc.stdout)


if __name__ == "__main__":
    unittest.main()
