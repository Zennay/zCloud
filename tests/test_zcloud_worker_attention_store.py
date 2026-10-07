from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_worker_attention_store as store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_worker_attention_store.py"


def cycle(**overrides):
    payload = {
        "cycle_id": "cycle-001",
        "worker_id": "cloud::w1",
        "project_id": "cloud",
        "category": "feature_code",
        "occurred_at": "2026-10-07T11:00:00+00:00",
        "commit_count": 1,
        "code_change_count": 2,
        "milestone_move_count": 1,
        "blocker_removed_count": 0,
        "deploy_outcome_count": 0,
    }
    payload.update(overrides)
    return payload


class WorkerAttentionStoreTests(unittest.TestCase):
    def test_cycle_survives_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "attention.db"
            conn = store.open_store(db)
            store.record_cycle(conn, cycle())
            conn.close()

            reopened = store.open_store(db)
            result = store.query_window(
                reopened,
                start="2026-10-07T10:00:00Z",
                end="2026-10-07T12:00:00Z",
            )
            reopened.close()

            self.assertEqual(1, result["totals"]["cycles"])
            self.assertEqual(1, result["totals"]["commit_count"])
            self.assertEqual(2, result["totals"]["code_change_count"])
            self.assertEqual("feature_code", result["rows"][0]["category"])
            self.assertFalse(result["free_text_stored"])

    def test_all_required_categories_are_persistable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "attention.db"
            conn = store.open_store(db)
            for index, category in enumerate(sorted(store.CATEGORIES), start=1):
                store.record_cycle(
                    conn,
                    cycle(
                        cycle_id=f"cycle-{index:03d}",
                        category=category,
                        occurred_at=f"2026-10-07T11:{index:02d}:00Z",
                    ),
                )
            result = store.query_window(
                conn,
                start="2026-10-07T11:00:00Z",
                end="2026-10-07T12:00:00Z",
            )
            conn.close()
            self.assertEqual(store.CATEGORIES, {row["category"] for row in result["rows"]})

    def test_unknown_or_free_text_fields_fail_closed(self):
        with self.assertRaisesRegex(store.AttentionStoreError, "cycle_keys_invalid"):
            store.normalize_cycle({**cycle(), "prompt": "secret free text"})
        with self.assertRaisesRegex(store.AttentionStoreError, "cycle_keys_invalid"):
            store.normalize_cycle({**cycle(), "reason": "waiting for CI"})

    def test_invalid_category_and_unbounded_counters_rejected(self):
        with self.assertRaisesRegex(store.AttentionStoreError, "category_invalid"):
            store.normalize_cycle(cycle(category="random_status"))
        with self.assertRaisesRegex(store.AttentionStoreError, "commit_count_invalid"):
            store.normalize_cycle(cycle(commit_count=-1))
        with self.assertRaisesRegex(store.AttentionStoreError, "commit_count_invalid"):
            store.normalize_cycle(cycle(commit_count=True))

    def test_duplicate_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "attention.db"
            conn = store.open_store(db)
            store.record_cycle(conn, cycle())
            with self.assertRaisesRegex(store.AttentionStoreError, "cycle_duplicate"):
                store.record_cycle(conn, cycle())
            conn.close()

    def test_window_filter_and_worker_filter_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "attention.db"
            conn = store.open_store(db)
            store.record_cycle(conn, cycle())
            store.record_cycle(
                conn,
                cycle(
                    cycle_id="cycle-002",
                    worker_id="supa::w1",
                    project_id="supa",
                    category="validation",
                    occurred_at="2026-10-07T11:30:00Z",
                    commit_count=0,
                    code_change_count=0,
                    milestone_move_count=0,
                ),
            )
            result = store.query_window(
                conn,
                start="2026-10-07T10:00:00Z",
                end="2026-10-07T12:00:00Z",
                worker_id="cloud::w1",
            )
            conn.close()
            self.assertEqual(1, result["totals"]["cycles"])
            self.assertEqual("cloud::w1", result["rows"][0]["worker_id"])

    def test_schema_contains_no_free_text_payload_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "attention.db"
            conn = store.open_store(db)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(worker_attention_cycles)").fetchall()
            }
            conn.close()
            self.assertEqual(store.ROW_KEYS, columns)
            for forbidden in ("prompt", "reason", "error", "title", "conversation_id", "result"):
                self.assertNotIn(forbidden, columns)

    def test_symlink_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.db"
            sqlite3.connect(target).close()
            link = root / "link.db"
            link.symlink_to(target)
            with self.assertRaisesRegex(store.AttentionStoreError, "db_symlink_rejected"):
                store.open_store(link)

    def test_cli_init_record_query_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "attention.db"
            payload = root / "cycle.json"
            payload.write_text(json.dumps(cycle()), encoding="utf-8")

            init = subprocess.run(
                [sys.executable, str(SCRIPT), "--db", str(db), "init"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, init.returncode, init.stderr)

            record = subprocess.run(
                [sys.executable, str(SCRIPT), "--db", str(db), "record", str(payload)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, record.returncode, record.stderr)

            query = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--db",
                    str(db),
                    "query",
                    "--start",
                    "2026-10-07T10:00:00Z",
                    "--end",
                    "2026-10-07T12:00:00Z",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, query.returncode, query.stderr)
            result = json.loads(query.stdout)
            self.assertEqual(1, result["totals"]["cycles"])
            self.assertFalse(result["free_text_stored"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
