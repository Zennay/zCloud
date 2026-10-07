from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts import zcloud_worker_vps_wait_detector as detector

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_worker_vps_wait_detector.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-vps-wait-detector.yml"


class WorkerVpsWaitDetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-vps-wait-")
        self.db = Path(self.tmp.name) / "history.db"
        self.now = dt.datetime(2026, 10, 7, 10, 30, tzinfo=dt.timezone.utc)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    project_id TEXT,
                    worker_slot INTEGER,
                    generating INTEGER,
                    sending INTEGER,
                    reason TEXT,
                    error TEXT
                )"""
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def emit(
        self,
        project: str,
        slot: int,
        minutes_ago: int,
        event: str,
        *,
        generating: int = 0,
        sending: int = 0,
    ):
        ts = (self.now - dt.timedelta(minutes=minutes_ago)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                "VALUES(?,?,?,?,?,?)",
                (ts, event, project, slot, generating, sending),
            )
            conn.commit()

    def test_repeated_wait_without_progress_is_delegate_candidate(self):
        self.emit("cloud", 1, 15, "generation-finished")
        self.emit("cloud", 1, 10, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "autonomy-wait-vps")

        result = detector.detect_vps_wait(self.db, now=self.now)
        self.assertEqual(1, result["delegate_candidate_count"])
        candidate = result["candidates"][0]
        self.assertEqual("cloud::w1", candidate["worker_id"])
        self.assertEqual(2, candidate["consecutive_vps_wait_signals"])
        self.assertEqual("repeated_vps_wait_no_progress", candidate["reason_code"])
        self.assertEqual("delegate_vps_wait_and_continue", candidate["routing_advice"])
        self.assertFalse(result["mutation_performed"])

    def test_progress_after_wait_clears_candidate(self):
        self.emit("cloud", 1, 15, "autonomy-wait-vps")
        self.emit("cloud", 1, 10, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "prompt-sent")

        result = detector.detect_vps_wait(self.db, now=self.now)
        self.assertEqual(0, result["delegate_candidate_count"])
        worker = result["workers"][0]
        self.assertEqual(0, worker["consecutive_vps_wait_signals"])
        self.assertEqual("prompt-sent", worker["latest_relevant_event"])

    def test_single_wait_is_not_enough(self):
        self.emit("supa", 2, 5, "autonomy-wait-vps")
        result = detector.detect_vps_wait(self.db, now=self.now)
        self.assertEqual(0, result["delegate_candidate_count"])
        self.assertEqual(1, result["workers"][0]["consecutive_vps_wait_signals"])

    def test_active_wait_signal_fails_safe_not_candidate(self):
        self.emit("cloud", 1, 10, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "autonomy-wait-vps", generating=1)
        result = detector.detect_vps_wait(self.db, now=self.now)
        self.assertEqual(0, result["delegate_candidate_count"])
        self.assertEqual(0, result["workers"][0]["consecutive_vps_wait_signals"])

    def test_workers_are_isolated(self):
        self.emit("cloud", 1, 12, "autonomy-wait-vps")
        self.emit("cloud", 1, 8, "autonomy-wait-vps")
        self.emit("supa", 2, 10, "autonomy-wait-vps")
        self.emit("supa", 2, 5, "generation-started")

        result = detector.detect_vps_wait(self.db, now=self.now)
        self.assertEqual(["cloud::w1"], [row["worker_id"] for row in result["candidates"]])

    def test_outside_window_does_not_count(self):
        self.emit("cloud", 1, 180, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "autonomy-wait-vps")
        result = detector.detect_vps_wait(self.db, hours=2, now=self.now)
        self.assertEqual(0, result["delegate_candidate_count"])

    def test_invalid_schema_fails_closed(self):
        other = Path(self.tmp.name) / "bad.db"
        with sqlite3.connect(other) as conn:
            conn.execute("CREATE TABLE runner_events(id INTEGER PRIMARY KEY, ts TEXT)")
        with self.assertRaisesRegex(
            detector.WaitEvidenceError, "runner_events_columns_incomplete"
        ):
            detector.detect_vps_wait(other, now=self.now)

    def test_symlink_db_is_rejected(self):
        link = Path(self.tmp.name) / "link.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(detector.WaitEvidenceError, "db_symlink_rejected"):
            detector.detect_vps_wait(link, now=self.now)

    def test_inventory_overflow_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            rows = [
                (
                    (self.now - dt.timedelta(seconds=index)).isoformat(),
                    "autonomy-wait-vps",
                    "cloud",
                    1,
                    0,
                    0,
                )
                for index in range(detector.MAX_ROWS + 1)
            ]
            conn.executemany(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) "
                "VALUES(?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
        with self.assertRaisesRegex(detector.WaitEvidenceError, "event_inventory_overflow"):
            detector.detect_vps_wait(self.db, now=self.now)

    def test_read_only_query_does_not_change_database_file(self):
        self.emit("cloud", 1, 10, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "autonomy-wait-vps")
        before = self.db.stat().st_mtime_ns
        detector.detect_vps_wait(self.db, now=self.now)
        after = self.db.stat().st_mtime_ns
        self.assertEqual(before, after)

    def test_cli_output_is_bounded_and_free_of_raw_fields(self):
        self.emit("cloud", 1, 10, "autonomy-wait-vps")
        self.emit("cloud", 1, 5, "autonomy-wait-vps")
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(self.db),
                "--hours",
                "2",
                "--require-compatible",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        encoded = json.dumps(payload, sort_keys=True).lower()
        self.assertEqual(detector.POLICY, payload["policy"])
        self.assertFalse(payload["mutation_performed"])
        for forbidden in ("reason", "error", "target", "title", "prompt", "conversation"):
            self.assertNotIn(f'"{forbidden}"', encoded)

    def test_workflow_is_exact_head_read_only_and_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("--db", text)
        self.assertIn("--require-compatible", text)
        self.assertIn("ZCLOUD_WORKER_VPS_WAIT_DETECTOR_GREEN=", text)
        self.assertIn("retention-days: 14", text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "sudo ",
            "systemctl ",
            "git push",
            "INSERT INTO",
            "UPDATE runner_events",
            "DELETE FROM runner_events",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
