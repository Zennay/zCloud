"""Regression: terminal SQLite assignments must never re-enter worker dispatch.

This uses a disposable SQLite database; it never opens the live VPS queue.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TerminalQueueDispatchRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(server, "DB", self.root / "history.db"),
            patch.object(server, "PORTFOLIO_QUEUE_SEED_FILE", self.root / "seed.json"),
            patch.object(server, "GLOBAL_CHATGPT_WORKER_LIMIT", 1),
            patch.object(server, "MAX_CHATGPT_WORKERS", 1),
            patch.object(server, "DYNAMIC_CHATGPT_WORKERS", 1),
            patch.object(server, "DYNAMIC_CLAUDE_WORKERS", 0),
            patch.object(server, "worker_memory_status", return_value={
                "available_mb": 16384, "total_mb": 32768,
                "swap_total_mb": 4096, "swap_free_mb": 4096,
                "headroom_mb": 2048, "effective_headroom_mb": 2048,
                "per_new_slot_mb": 1536, "new_worker_capacity": 8,
                "pressure": "ok", "healthy_for_new_worker": True,
                "swap_healthy": True,
            }),
        ]
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)
        self.addCleanup(self.tmp.cleanup)
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()

    def test_terminal_row_does_not_redispatch_even_when_eligible_stale(self):
        previous = server.portfolio_queue_enqueue(
            "cloud", "Previously completed implementation", "P0",
            "Implement and test a completed scoped change.",
        )
        with server.connect() as conn:
            # Deliberately leave eligible=1, reproducing a stale eligibility flag.
            conn.execute(
                "UPDATE portfolio_queue SET status='done', eligible=1 WHERE queue_id=?",
                (previous["queue_id"],),
            )
        fresh = server.portfolio_queue_enqueue(
            "cloud", "New independent implementation", "P1",
            "Implement and test a different scoped change.",
        )
        for _ in range(3):
            selected = server.portfolio_queue_allocate()
            ids = {item["queue_id"] for item in selected}
            self.assertNotIn(previous["queue_id"], ids)
            self.assertIn(fresh["queue_id"], ids)

    def test_only_terminal_rows_result_in_no_new_worker_claims(self):
        previous = server.portfolio_queue_enqueue(
            "cloud", "Already completed change", "P0",
            "Implement and test this completed change.",
        )
        with server.connect() as conn:
            conn.execute(
                "UPDATE portfolio_queue SET status='done', eligible=1 WHERE queue_id=?",
                (previous["queue_id"],),
            )
        for _ in range(3):
            self.assertEqual([], server.portfolio_queue_allocate())


if __name__ == "__main__":
    unittest.main()
