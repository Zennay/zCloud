from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import zcloud_project_bottleneck_report as report


NOW = datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


class ProjectBottleneckReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = self.root / "history.db"
        self.projects = self.root / "projects.json"
        self.contracts = self.root / "project-contracts.json"
        self.project_ids = [
            "human",
            "attention",
            "receipt",
            "stale",
            "waiting",
            "saturated",
            "running",
            "idle",
        ]
        self.projects.write_text(
            json.dumps(
                [{"id": project_id, "status": "active"} for project_id in self.project_ids]
            ),
            encoding="utf-8",
        )
        contracts = {}
        for project_id in self.project_ids:
            contracts[project_id] = {
                "queue_mode": "human-gated" if project_id == "human" else "automatic",
                "ai_worker_cap": 0 if project_id == "human" else 1,
            }
        self.contracts.write_text(
            json.dumps({"schema_version": 1, "projects": contracts}),
            encoding="utf-8",
        )

        with sqlite3.connect(self.db) as connection:
            connection.executescript(
                """
                CREATE TABLE portfolio_queue(
                    queue_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    blocker TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE portfolio_attention(
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL
                );
                CREATE TABLE ai_global_slots(
                    slot INTEGER PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    worker_slot INTEGER NOT NULL
                );
                CREATE TABLE task_claims(
                    project_id TEXT NOT NULL,
                    lease_until TEXT NOT NULL
                );
                CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    ci_status TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO portfolio_attention(project_id,status,severity) VALUES(?,?,?)",
                ("attention", "open", "urgent"),
            )
            connection.execute(
                "INSERT INTO project_state_receipts(project_id,ci_status,blocker,observed_at) "
                "VALUES(?,?,?,?)",
                (
                    "receipt",
                    "failure",
                    "super-secret-token-value",
                    "2026-10-06T10:58:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,project_id,status,eligible,updated_at,blocker) "
                "VALUES(?,?,?,?,?,?)",
                ("q-stale", "stale", "running", 0, "2026-10-06T10:00:00+00:00", ""),
            )
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,project_id,status,eligible,updated_at,blocker) "
                "VALUES(?,?,?,?,?,?)",
                ("q-wait", "waiting", "queued", 1, "2026-10-06T10:59:00+00:00", ""),
            )
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,project_id,status,eligible,updated_at,blocker) "
                "VALUES(?,?,?,?,?,?)",
                (
                    "q-saturated",
                    "saturated",
                    "queued",
                    1,
                    "2026-10-06T10:59:00+00:00",
                    "",
                ),
            )
            connection.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot) VALUES(?,?,?)",
                (1, "saturated", 1),
            )
            connection.execute(
                "INSERT INTO portfolio_queue(queue_id,project_id,status,eligible,updated_at,blocker) "
                "VALUES(?,?,?,?,?,?)",
                (
                    "q-running",
                    "running",
                    "running",
                    0,
                    "2026-10-06T10:55:00+00:00",
                    "",
                ),
            )
            connection.execute(
                "INSERT INTO ai_global_slots(slot,project_id,worker_slot) VALUES(?,?,?)",
                (2, "running", 1),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _report(self) -> dict:
        return report.build_report(
            self.db,
            self.projects,
            self.contracts,
            stale_minutes=20,
            now=NOW,
        )

    def test_classifies_bottlenecks_from_bounded_runtime_evidence(self) -> None:
        payload = self._report()
        by_id = {item["project_id"]: item for item in payload["projects"]}

        self.assertEqual("external_or_human_gate", by_id["human"]["bottleneck_code"])
        self.assertEqual("human_attention_required", by_id["attention"]["bottleneck_code"])
        self.assertEqual("receipt_blocker_present", by_id["receipt"]["bottleneck_code"])
        self.assertEqual("active_work_stale", by_id["stale"]["bottleneck_code"])
        self.assertEqual(
            "runnable_work_waiting_for_worker",
            by_id["waiting"]["bottleneck_code"],
        )
        self.assertEqual(
            "project_worker_cap_saturated",
            by_id["saturated"]["bottleneck_code"],
        )
        self.assertEqual("active_execution", by_id["running"]["bottleneck_code"])
        self.assertEqual("no_runnable_work", by_id["idle"]["bottleneck_code"])

        self.assertEqual(4, payload["counts"]["blocked"])
        self.assertEqual(2, payload["counts"]["waiting"])
        self.assertEqual(1, payload["counts"]["running"])
        self.assertEqual(1, payload["counts"]["idle"])

    def test_does_not_emit_raw_blocker_or_queue_payloads(self) -> None:
        serialized = json.dumps(self._report(), sort_keys=True)
        self.assertNotIn("super-secret-token-value", serialized)
        self.assertNotIn("blocker_text", serialized)
        self.assertIn('"receipt_blocker_present": true', serialized)

    def test_project_filter_is_bounded_and_deduplicated(self) -> None:
        payload = report.build_report(
            self.db,
            self.projects,
            self.contracts,
            project_ids=["idle", "idle", "waiting"],
            now=NOW,
        )
        self.assertEqual(["idle", "waiting"], [item["project_id"] for item in payload["projects"]])

        with self.assertRaisesRegex(ValueError, "unknown or archived project"):
            report.build_report(
                self.db,
                self.projects,
                self.contracts,
                project_ids=["missing"],
                now=NOW,
            )

    def test_report_preserves_database_bytes_and_mtime(self) -> None:
        before_bytes = self.db.read_bytes()
        before_mtime = self.db.stat().st_mtime_ns

        self._report()

        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_mtime, self.db.stat().st_mtime_ns)

    def test_malformed_relevant_timestamp_fails_closed(self) -> None:
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "UPDATE portfolio_queue SET updated_at='not-a-time' WHERE project_id='running'"
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "timestamp invalid"):
            self._report()

    def test_symlink_database_is_rejected(self) -> None:
        link = self.root / "history-link.db"
        try:
            link.symlink_to(self.db)
        except OSError:
            self.skipTest("symlinks unavailable")

        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            report.build_report(
                link,
                self.projects,
                self.contracts,
                now=NOW,
            )

    def test_missing_runtime_contract_fails_closed(self) -> None:
        contracts = json.loads(self.contracts.read_text(encoding="utf-8"))
        del contracts["projects"]["idle"]
        self.contracts.write_text(json.dumps(contracts), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "missing runtime contracts"):
            self._report()


if __name__ == "__main__":
    unittest.main()
