import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent(
    r"""
    import json
    import sys
    from pathlib import Path

    import server

    mode = sys.argv[1]
    db_path = Path(sys.argv[2])
    seed_path = Path(sys.argv[3])

    server.DB = db_path
    server.PORTFOLIO_QUEUE_SEED_FILE = seed_path
    server.GLOBAL_CHATGPT_WORKER_LIMIT = 8
    server.MAX_CHATGPT_WORKERS = 8
    server.DYNAMIC_CHATGPT_WORKERS = 6
    server.DYNAMIC_CLAUDE_WORKERS = 2
    server.init_db()

    if mode == "seed":
        with server.connect() as conn:
            conn.execute("DELETE FROM runner_workers WHERE project_id='ftmo'")
            conn.execute("DELETE FROM ai_global_slots")
            conn.execute(
                "UPDATE runner_targets SET active=1,worker_count=2,conversation_id=? WHERE project_id='ftmo'",
                ("11111111-1111-4111-8111-111111111111",),
            )
            conn.execute(
                """INSERT INTO runner_workers(
                       project_id,worker_slot,conversation_id,desired_state,provider
                   ) VALUES(?,?,?,?,?)""",
                (
                    "ftmo",
                    1,
                    "11111111-1111-4111-8111-111111111111",
                    "running",
                    "chatgpt",
                ),
            )
            conn.execute(
                """INSERT INTO runner_workers(
                       project_id,worker_slot,conversation_id,desired_state,provider
                   ) VALUES(?,?,?,?,?)""",
                (
                    "ftmo",
                    2,
                    "22222222-2222-4222-8222-222222222222",
                    "draining",
                    "chatgpt",
                ),
            )
            conn.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at) VALUES(1,'ftmo',1,?)",
                (server.now(),),
            )
            conn.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at) VALUES(2,'ftmo',2,?)",
                (server.now(),),
            )
            rows = conn.execute(
                """SELECT project_id,worker_slot,conversation_id,desired_state,provider
                   FROM runner_workers WHERE project_id='ftmo' ORDER BY worker_slot"""
            ).fetchall()
        print(json.dumps({"rows": [dict(row) for row in rows]}, sort_keys=True))
        raise SystemExit(0)

    if mode != "read":
        raise SystemExit(f"unknown mode: {mode}")

    base = {
        "ftmo": {
            "project_id": "ftmo",
            "name": "FTMO",
            "conversation_id": "11111111-1111-4111-8111-111111111111",
            "prompt": "restart mapping regression",
            "active": True,
            "worker_count": 2,
            "auto_continue": True,
            "auto_continue_delay_seconds": 0,
            "vps_dispatch_only": True,
            "ai_dispatch_interval_seconds": 0,
            "autonomy": {},
            "improvement": None,
        }
    }
    allocation = {
        "workers": [
            {
                "worker_key": "ftmo::w1",
                "project_id": "ftmo",
                "global_worker_slot": 1,
                "queue_id": "",
            },
            {
                "worker_key": "ftmo::w2",
                "project_id": "ftmo",
                "global_worker_slot": 2,
                "queue_id": "",
            },
        ]
    }
    targets = server.runner_worker_targets(
        allocation=allocation,
        base=base,
        reconcile=False,
    )
    with server.connect() as conn:
        rows = conn.execute(
            """SELECT project_id,worker_slot,conversation_id,desired_state,provider
               FROM runner_workers WHERE project_id='ftmo' ORDER BY worker_slot"""
        ).fetchall()

    payload = {
        "rows": [dict(row) for row in rows],
        "targets": {
            key: {
                "base_project_id": value["base_project_id"],
                "worker_slot": value["worker_slot"],
                "global_worker_slot": value["global_worker_slot"],
                "conversation_id": value["conversation_id"],
                "desired_state": value["desired_state"],
                "provider": value["provider"],
            }
            for key, value in targets.items()
            if key.startswith("ftmo::")
        },
    }
    print(json.dumps(payload, sort_keys=True))
    """
)


class ServiceRestartMappingTests(unittest.TestCase):
    def run_child(self, mode: str, db_path: Path, seed_path: Path) -> dict:
        proc = subprocess.run(
            [sys.executable, "-c", CHILD, mode, str(db_path), str(seed_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def test_process_restart_preserves_worker_conversation_mapping(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-service-restart-") as td:
            root = Path(td)
            db_path = root / "history.db"
            seed_path = root / "portfolio-queue-seed.json"
            seed_path.write_text("[]\n", encoding="utf-8")

            before = self.run_child("seed", db_path, seed_path)
            after = self.run_child("read", db_path, seed_path)

            self.assertEqual(before["rows"], after["rows"])
            self.assertEqual(
                {
                    "ftmo::w1": {
                        "base_project_id": "ftmo",
                        "worker_slot": 1,
                        "global_worker_slot": 1,
                        "conversation_id": "11111111-1111-4111-8111-111111111111",
                        "desired_state": "running",
                        "provider": "chatgpt",
                    },
                    "ftmo::w2": {
                        "base_project_id": "ftmo",
                        "worker_slot": 2,
                        "global_worker_slot": 2,
                        "conversation_id": "22222222-2222-4222-8222-222222222222",
                        "desired_state": "draining",
                        "provider": "chatgpt",
                    },
                },
                after["targets"],
            )

    def test_repeated_restart_is_idempotent_and_read_only(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-service-restart-") as td:
            root = Path(td)
            db_path = root / "history.db"
            seed_path = root / "portfolio-queue-seed.json"
            seed_path.write_text("[]\n", encoding="utf-8")

            seeded = self.run_child("seed", db_path, seed_path)
            first = self.run_child("read", db_path, seed_path)
            second = self.run_child("read", db_path, seed_path)

            self.assertEqual(seeded["rows"], first["rows"])
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
