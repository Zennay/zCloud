from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_queue_seed_reseed_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_queue_seed_reseed_audit.py"


def make_db(path: Path, rows=()):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE portfolio_queue("
        "queue_id TEXT PRIMARY KEY, project_id TEXT, status TEXT, eligible INTEGER)"
    )
    connection.executemany(
        "INSERT INTO portfolio_queue(queue_id,project_id,status,eligible) VALUES(?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()


class QueueSeedReseedAuditTests(unittest.TestCase):
    def test_absent_queued_eligible_seed_is_reseed_risk(self):
        report = audit.build_report(
            [
                {
                    "queue_id": "cloud-old-work",
                    "project_id": "cloud",
                    "title": "Old work",
                    "status": "queued",
                    "eligible": True,
                }
            ],
            {},
        )

        self.assertFalse(report["safe"])
        self.assertEqual(1, report["runnable_reseed_risk_count"])
        self.assertEqual(["cloud-old-work"], report["runnable_reseed_risk_ids"])
        self.assertEqual("absent_reseed_risk", report["items"][0]["classification"])

    def test_present_terminal_runtime_row_prevents_reseed(self):
        report = audit.build_report(
            [
                {
                    "queue_id": "cloud-done",
                    "project_id": "cloud",
                    "status": "queued",
                    "eligible": True,
                }
            ],
            {
                "cloud-done": {
                    "project_id": "cloud",
                    "status": "completed",
                    "eligible": False,
                }
            },
        )

        self.assertTrue(report["safe"])
        self.assertEqual("present_runtime_row", report["items"][0]["classification"])
        self.assertEqual("completed", report["items"][0]["runtime_status"])
        self.assertFalse(report["items"][0]["would_reseed_runnable"])

    def test_absent_noneligible_seed_is_not_runnable_risk(self):
        report = audit.build_report(
            [
                {
                    "queue_id": "external-gate",
                    "project_id": "zssh",
                    "status": "queued",
                    "eligible": False,
                }
            ],
            {},
        )

        self.assertTrue(report["safe"])
        self.assertEqual("absent_non_runnable", report["items"][0]["classification"])

    def test_duplicate_seed_id_fails_closed(self):
        row = {"queue_id": "dup", "project_id": "cloud", "status": "queued"}
        with self.assertRaisesRegex(audit.QueueSeedAuditError, "duplicate queue seed id"):
            audit.build_report([row, dict(row)], {})

    def test_invalid_seed_project_id_fails_closed(self):
        with self.assertRaisesRegex(audit.QueueSeedAuditError, "invalid project id"):
            audit.build_report(
                [{"queue_id": "bad", "project_id": "../cloud", "status": "queued"}],
                {},
            )

    def test_runtime_database_is_opened_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "history.db"
            make_db(db, [("q1", "cloud", "completed", 0)])
            before = db.read_bytes()

            rows = audit.load_runtime_rows(db)

            self.assertEqual("completed", rows["q1"]["status"])
            self.assertEqual(before, db.read_bytes())
            source = SCRIPT.read_text(encoding="utf-8")
            self.assertIn("?mode=ro", source)
            self.assertIn("PRAGMA query_only=ON", source)

    def test_seed_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.json"
            link = root / "seed.json"
            real.write_text("[]", encoding="utf-8")
            link.symlink_to(real)
            with self.assertRaisesRegex(audit.QueueSeedAuditError, "non-symlink"):
                audit.load_seed(link)

    def test_cli_require_safe_exits_three_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed.json"
            db = root / "history.db"
            seed.write_text(
                json.dumps(
                    [
                        {
                            "queue_id": "missing-work",
                            "project_id": "cloud",
                            "status": "queued",
                            "eligible": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            make_db(db)
            before = db.read_bytes()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--seed",
                    str(seed),
                    "--db",
                    str(db),
                    "--require-safe",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(3, completed.returncode)
            self.assertEqual(1, payload["runnable_reseed_risk_count"])
            self.assertEqual(before, db.read_bytes())


if __name__ == "__main__":
    unittest.main()
