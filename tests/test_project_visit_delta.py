import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_project_visit_delta.py"
SPEC = importlib.util.spec_from_file_location("zcloud_project_visit_delta", SCRIPT)
delta = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(delta)


class ProjectVisitDeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-visit-delta-")
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    commit_sha TEXT NOT NULL DEFAULT '',
                    ci_status TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '',
                    next_gate TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )"""
            )

    def tearDown(self):
        self.tmp.cleanup()

    def add(
        self,
        observed_at,
        *,
        project="cloud",
        phase="P1",
        action="",
        commit_sha="",
        ci_status="",
        blocker="",
        next_gate="",
        source="receipt",
        evidence=None,
    ):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO project_state_receipts(
                    project_id,phase,action,commit_sha,ci_status,blocker,next_gate,
                    source,observed_at,evidence_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project,
                    phase,
                    action,
                    commit_sha,
                    ci_status,
                    blocker,
                    next_gate,
                    source,
                    observed_at,
                    json.dumps(evidence or {}),
                    observed_at,
                ),
            )

    def test_reports_semantic_changes_since_visit_without_evidence_payload(self):
        self.add(
            "2026-10-06T08:00:00+00:00",
            action="baseline",
            commit_sha="aaa",
            ci_status="success",
            next_gate="build",
            evidence={"secretish": "must-not-leak"},
        )
        self.add(
            "2026-10-06T09:00:00+00:00",
            action="built",
            commit_sha="bbb",
            ci_status="success",
            next_gate="validate",
            evidence={"raw": "do-not-export"},
        )
        self.add(
            "2026-10-06T09:30:00+00:00",
            action="built",
            commit_sha="bbb",
            ci_status="success",
            next_gate="validate",
            evidence={"different": "but-semantically-identical"},
        )
        self.add(
            "2026-10-06T10:00:00+00:00",
            action="validated",
            commit_sha="bbb",
            ci_status="success",
            next_gate="merge",
            evidence={"raw": "still-private"},
        )

        payload = delta.build_delta(
            self.db,
            project_id="cloud",
            since="2026-10-06T08:30:00Z",
            limit=50,
        )

        self.assertEqual(2, payload["change_count"])
        self.assertTrue(payload["has_changes"])
        self.assertEqual(payload["current"]["id"], payload["cursor_receipt_id"])
        self.assertEqual("baseline", payload["baseline"]["action"])
        self.assertEqual(["action", "commit_sha", "next_gate"], payload["changes"][0]["changed_fields"])
        self.assertEqual("validated", payload["current"]["action"])
        encoded = json.dumps(payload)
        self.assertNotIn("secretish", encoded)
        self.assertNotIn("do-not-export", encoded)
        self.assertNotIn("still-private", encoded)
        self.assertNotIn("evidence_json", encoded)

    def test_project_isolation_and_limit_bounds(self):
        self.add("2026-10-06T09:00:00+00:00", project="ftmo", action="other")
        self.add("2026-10-06T09:05:00+00:00", action="cloud")
        payload = delta.build_delta(
            self.db,
            project_id="cloud",
            since="2026-10-06T08:00:00+00:00",
            limit=1,
        )
        self.assertEqual(1, payload["change_count"])
        self.assertEqual("cloud", payload["current"]["action"])
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            delta.build_delta(self.db, project_id="cloud", since="2026-10-06T08:00:00Z", limit=0)
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            delta.build_delta(self.db, project_id="cloud", since="2026-10-06T08:00:00Z", limit=201)

    def test_requires_timezone_aware_since(self):
        with self.assertRaisesRegex(ValueError, "include a timezone"):
            delta.build_delta(
                self.db,
                project_id="cloud",
                since="2026-10-06T08:00:00",
            )

    def test_rejects_symlink_database_path(self):
        link = Path(self.tmp.name) / "linked.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(ValueError, "symlink"):
            delta.build_delta(
                link,
                project_id="cloud",
                since="2026-10-06T08:00:00Z",
            )

    def test_read_only_execution_preserves_database_bytes_and_mtime(self):
        self.add("2026-10-06T09:00:00+00:00", action="observed")
        before = self.db.read_bytes()
        before_stat = self.db.stat()
        payload = delta.build_delta(
            self.db,
            project_id="cloud",
            since="2026-10-06T08:00:00Z",
        )
        after_stat = self.db.stat()
        self.assertEqual(1, payload["change_count"])
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_missing_receipt_schema_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        with sqlite3.connect(broken) as conn:
            conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
        with self.assertRaisesRegex(ValueError, "project_state_receipts table missing"):
            delta.build_delta(
                broken,
                project_id="cloud",
                since="2026-10-06T08:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
