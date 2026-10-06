import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]


def snapshot(db_path: Path) -> dict:
    with server.connect() as conn:
        target = dict(conn.execute(
            "SELECT project_id,conversation_id,active,worker_count "
            "FROM runner_targets WHERE project_id='cloud'"
        ).fetchone())
        workers = [
            dict(row)
            for row in conn.execute(
                "SELECT project_id,worker_slot,conversation_id,desired_state,provider "
                "FROM runner_workers WHERE project_id='cloud' ORDER BY worker_slot"
            )
        ]
    return {"target": target, "workers": workers}


class ServiceRestartWorkerMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-restart-workermap-")
        self.saved = {
            "DB": server.DB,
            "PORTFOLIO_QUEUE_SEED_FILE": server.PORTFOLIO_QUEUE_SEED_FILE,
            "GLOBAL_CHATGPT_WORKER_LIMIT": server.GLOBAL_CHATGPT_WORKER_LIMIT,
            "MAX_CHATGPT_WORKERS": server.MAX_CHATGPT_WORKERS,
            "DYNAMIC_CHATGPT_WORKERS": server.DYNAMIC_CHATGPT_WORKERS,
            "DYNAMIC_CLAUDE_WORKERS": server.DYNAMIC_CLAUDE_WORKERS,
        }
        root = Path(self.tmp.name)
        server.DB = root / "history.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]\n", encoding="utf-8")
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.MAX_CHATGPT_WORKERS = 8
        server.DYNAMIC_CHATGPT_WORKERS = 2
        server.DYNAMIC_CLAUDE_WORKERS = 1
        server.init_db()

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(server, key, value)
        self.tmp.cleanup()

    def seed_distinct_worker_map(self):
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_targets "
                "SET conversation_id=?, active=1, worker_count=2 "
                "WHERE project_id='cloud'",
                ("cloud-primary-persistent-conversation",),
            )
            conn.execute(
                "UPDATE runner_workers "
                "SET conversation_id=?, desired_state='running', provider='chatgpt' "
                "WHERE project_id='cloud' AND worker_slot=1",
                ("cloud-worker-1-persistent-conversation",),
            )
            conn.execute(
                "INSERT INTO runner_workers("
                "project_id,worker_slot,conversation_id,desired_state,provider"
                ") VALUES(?,?,?,?,?) "
                "ON CONFLICT(project_id,worker_slot) DO UPDATE SET "
                "conversation_id=excluded.conversation_id,"
                "desired_state=excluded.desired_state,"
                "provider=excluded.provider",
                (
                    "cloud",
                    2,
                    "cloud-worker-2-persistent-conversation",
                    "paused",
                    "claude",
                ),
            )

    def fresh_process_restart_snapshot(self) -> dict:
        code = r"""
import json
import sys
from pathlib import Path
import server

db = Path(sys.argv[1])
seed = Path(sys.argv[2])
server.DB = db
server.PORTFOLIO_QUEUE_SEED_FILE = seed
server.init_db()
with server.connect() as conn:
    target = dict(conn.execute(
        "SELECT project_id,conversation_id,active,worker_count "
        "FROM runner_targets WHERE project_id='cloud'"
    ).fetchone())
    workers = [
        dict(row)
        for row in conn.execute(
            "SELECT project_id,worker_slot,conversation_id,desired_state,provider "
            "FROM runner_workers WHERE project_id='cloud' ORDER BY worker_slot"
        )
    ]
print(json.dumps({"target": target, "workers": workers}, sort_keys=True))
"""
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(server.DB),
                str(server.PORTFOLIO_QUEUE_SEED_FILE),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_fresh_service_process_preserves_project_and_worker_map(self):
        self.seed_distinct_worker_map()
        before = snapshot(server.DB)

        after = self.fresh_process_restart_snapshot()

        self.assertEqual(before, after)
        self.assertEqual(2, after["target"]["worker_count"])
        self.assertEqual(1, after["target"]["active"])
        self.assertEqual(
            "cloud-primary-persistent-conversation",
            after["target"]["conversation_id"],
        )
        self.assertEqual(
            [
                {
                    "project_id": "cloud",
                    "worker_slot": 1,
                    "conversation_id": "cloud-worker-1-persistent-conversation",
                    "desired_state": "running",
                    "provider": "chatgpt",
                },
                {
                    "project_id": "cloud",
                    "worker_slot": 2,
                    "conversation_id": "cloud-worker-2-persistent-conversation",
                    "desired_state": "paused",
                    "provider": "claude",
                },
            ],
            after["workers"],
        )

    def test_repeated_fresh_restarts_are_idempotent(self):
        self.seed_distinct_worker_map()
        expected = snapshot(server.DB)

        first = self.fresh_process_restart_snapshot()
        second = self.fresh_process_restart_snapshot()
        third = self.fresh_process_restart_snapshot()

        self.assertEqual(expected, first)
        self.assertEqual(first, second)
        self.assertEqual(second, third)


if __name__ == "__main__":
    unittest.main()
