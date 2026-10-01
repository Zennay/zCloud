"""Shared fixture: temp SQLite DB with three allocated workers (cloud, haxlab, raiseai)."""
import tempfile
from pathlib import Path

import server


class WorkerEnv:
    KEYS = ("DB", "PORTFOLIO_QUEUE_SEED_FILE", "GLOBAL_CHATGPT_WORKER_LIMIT",
            "MAX_CHATGPT_WORKERS", "DYNAMIC_CHATGPT_WORKERS", "DYNAMIC_CLAUDE_WORKERS")

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {key: getattr(server, key) for key in self.KEYS}
        root = Path(self.tmp.name)
        server.DB = root / "worker-env.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 3
        server.MAX_CHATGPT_WORKERS = 3
        server.DYNAMIC_CHATGPT_WORKERS = 2
        server.DYNAMIC_CLAUDE_WORKERS = 1
        server.init_db()
        server.portfolio_queue_enqueue("cloud", "Deploylane", "P1", "green deploy")
        server.portfolio_queue_enqueue("haxlab", "Evidence gate", "P2", "gate closed")
        server.portfolio_queue_enqueue("raiseai", "Provider live", "P3", "provider live")
        server.portfolio_queue_allocate()
        # in production the scheduler tick persists the slots; do the same here
        server._persist_global_worker_allocation(server.global_worker_allocation())
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(server, key, value)
        self.tmp.cleanup()
