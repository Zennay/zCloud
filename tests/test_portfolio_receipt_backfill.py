from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_backfill_project_receipt.py"


def _create_queue_db(path: Path, *, project_id: str | None = None, status: str = "running") -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE portfolio_queue(
                queue_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        if project_id:
            conn.execute(
                """INSERT INTO portfolio_queue(
                    queue_id,project_id,status,title,evidence,blocker,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"{project_id}-q1",
                    project_id,
                    status,
                    "Continue concrete roadmap work",
                    "{\"proof\":\"queue\"}",
                    "",
                    "2026-10-04T00:00:00+00:00",
                    "2026-10-04T00:01:00+00:00",
                ),
            )


class PortfolioReceiptBackfillTests(unittest.TestCase):
    def _run(self, runtime_root: Path, project_id: str):
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--runtime-root",
                str(runtime_root),
                "--project",
                project_id,
                "--source",
                "test:portfolio-receipt-backfill",
                "--workflow-run-id",
                "12345",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_execution_project_maps_running_queue_to_in_progress_without_queue_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp)
            (runtime_root / "projects.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "raiseai",
                            "status": "active",
                            "phase": "Device rollout",
                            "next_step": "Verify paired watch flow",
                            "milestone_revision": "test-v1",
                            "priority": "P1",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_path = runtime_root / "history.db"
            _create_queue_db(db_path, project_id="raiseai", status="running")

            result = self._run(runtime_root, "raiseai")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("PORTFOLIO_STATE_RECEIPT_BACKFILL_GREEN", result.stdout)

            with sqlite3.connect(db_path) as conn:
                receipt = conn.execute(
                    "SELECT project_id,ci_status,source,evidence_json "
                    "FROM project_state_receipts ORDER BY id DESC LIMIT 1"
                ).fetchone()
                queue = conn.execute(
                    "SELECT queue_id,status,title FROM portfolio_queue WHERE project_id='raiseai'"
                ).fetchone()
            self.assertEqual(("raiseai", "in_progress", "test:portfolio-receipt-backfill"), receipt[:3])
            evidence = json.loads(receipt[3])
            self.assertEqual("running", evidence["queue_status"])
            self.assertEqual(("raiseai-q1", "running", "Continue concrete roadmap work"), queue)

    def test_human_gated_project_records_skipped_without_queue_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp)
            (runtime_root / "projects.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "ulab",
                            "status": "active",
                            "phase": "External gate",
                            "next_step": "Wait for human prerequisite",
                            "milestone_revision": "test-v1",
                            "priority": "P1",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_path = runtime_root / "history.db"
            db_path.touch()

            result = self._run(runtime_root, "ulab")
            self.assertEqual(0, result.returncode, result.stderr)
            with sqlite3.connect(db_path) as conn:
                receipt = conn.execute(
                    "SELECT ci_status,blocker,evidence_json "
                    "FROM project_state_receipts ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertEqual("skipped", receipt[0])
            self.assertEqual("Wait for human prerequisite", receipt[1])
            self.assertEqual("human-gated", json.loads(receipt[2])["queue_mode"])

    def test_execution_project_fails_closed_without_queue_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp)
            (runtime_root / "projects.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "raiseai",
                            "status": "active",
                            "phase": "Device rollout",
                            "next_step": "Verify paired watch flow",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_path = runtime_root / "history.db"
            _create_queue_db(db_path)

            result = self._run(runtime_root, "raiseai")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("no raiseai portfolio queue evidence available", result.stderr)


if __name__ == "__main__":
    unittest.main()
