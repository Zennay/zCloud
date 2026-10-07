from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import server


class PortfolioCompletionReceiptContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-completion-receipt-")
        self.original_db = server.DB
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        self.original_continuation = server.portfolio_write_continuation

        root = Path(self.tmp.name)
        server.DB = root / "queue-receipt.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.portfolio_write_continuation = lambda *args, **kwargs: None
        server.init_db()

    def tearDown(self):
        server.portfolio_write_continuation = self.original_continuation
        server.DB = self.original_db
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_seed
        self.tmp.cleanup()

    def test_done_transition_records_immutable_queue_completion_receipt(self):
        item = server.portfolio_queue_enqueue(
            "cloud",
            "Measure one material control-plane completion",
            "P1",
            "Prove queue completion receipt provenance.",
        )
        queue_id = item["queue_id"]

        with server.connect() as connection:
            connection.execute(
                """UPDATE portfolio_queue
                   SET status='running',worker_slot=1,claimed_at=?,claim_expires=?,updated_at=?
                   WHERE queue_id=?""",
                (
                    "2026-10-06T09:00:00+00:00",
                    "2026-10-06T11:00:00+00:00",
                    "2026-10-06T09:00:00+00:00",
                    queue_id,
                ),
            )

        result = server.portfolio_queue_finish(
            1,
            queue_id,
            "DONE",
            "unit-test completion evidence",
        )
        self.assertTrue(result["updated"])
        self.assertEqual("DONE", result["result"])

        with server.connect() as connection:
            receipts = connection.execute(
                """SELECT id,project_id,ci_status,source,observed_at,evidence_json,created_at
                   FROM project_state_receipts
                   WHERE source=? ORDER BY id""",
                ("portfolio_queue:" + queue_id,),
            ).fetchall()

        self.assertEqual(1, len(receipts))
        receipt = dict(receipts[0])
        evidence = json.loads(receipt["evidence_json"])
        self.assertEqual("cloud", receipt["project_id"])
        self.assertEqual("success", receipt["ci_status"])
        self.assertEqual("portfolio_queue:" + queue_id, receipt["source"])
        self.assertEqual(queue_id, evidence["queue_id"])
        self.assertEqual("DONE", evidence["result"])
        datetime.fromisoformat(receipt["observed_at"])
        observed_at = receipt["observed_at"]
        receipt_id = receipt["id"]

        with server.connect() as connection:
            connection.execute(
                "UPDATE portfolio_queue SET evidence=?,updated_at=? WHERE queue_id=?",
                (
                    "later mutable queue metadata",
                    "2099-01-01T00:00:00+00:00",
                    queue_id,
                ),
            )
            after = connection.execute(
                """SELECT id,observed_at,evidence_json
                   FROM project_state_receipts WHERE id=?""",
                (receipt_id,),
            ).fetchone()

        self.assertEqual(receipt_id, after["id"])
        self.assertEqual(observed_at, after["observed_at"])
        self.assertEqual(evidence, json.loads(after["evidence_json"]))

    def test_continue_does_not_create_successful_done_receipt(self):
        item = server.portfolio_queue_enqueue(
            "cloud",
            "Continue control-plane work",
            "P1",
            "Prove non-terminal queue transitions are not throughput.",
        )
        queue_id = item["queue_id"]
        with server.connect() as connection:
            connection.execute(
                """UPDATE portfolio_queue
                   SET status='running',worker_slot=1
                   WHERE queue_id=?""",
                (queue_id,),
            )

        server.portfolio_queue_finish(
            1,
            queue_id,
            "CONTINUE",
            "more work remains",
        )

        with server.connect() as connection:
            rows = connection.execute(
                """SELECT ci_status,evidence_json FROM project_state_receipts
                   WHERE source=?""",
                ("portfolio_queue:" + queue_id,),
            ).fetchall()

        self.assertEqual(1, len(rows))
        self.assertEqual("in_progress", rows[0]["ci_status"])
        self.assertEqual("CONTINUE", json.loads(rows[0]["evidence_json"])["result"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
