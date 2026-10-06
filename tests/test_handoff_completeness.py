import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_handoff_completeness as handoff


class HandoffCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-handoff-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.projects = self.root / "projects.json"
        self.projects.write_text(
            json.dumps([
                {"id": "cloud", "status": "active"},
                {"id": "ftmo", "status": "active"},
                {"id": "archived", "status": "paused"},
            ]),
            encoding="utf-8",
        )
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""CREATE TABLE project_state_receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT '',
                commit_sha TEXT NOT NULL DEFAULT '',
                ci_status TEXT NOT NULL DEFAULT '',
                next_gate TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}'
            )""")
            c.commit()
        self.now = datetime(2026, 10, 6, 19, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def add_receipt(
        self,
        project,
        *,
        action="Changed bounded control-plane contract",
        commit="a" * 40,
        ci="success",
        next_gate="Run exact-head validation",
        evidence=None,
        observed_at=None,
    ):
        if evidence is None:
            evidence = {"workflow_run_id": "123", "marker": "GREEN"}
        if observed_at is None:
            observed_at = (self.now - timedelta(minutes=5)).isoformat()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(
                """INSERT INTO project_state_receipts(
                    project_id,action,commit_sha,ci_status,next_gate,observed_at,evidence_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (project, action, commit, ci, next_gate, observed_at, json.dumps(evidence)),
            )
            c.commit()

    def test_complete_latest_handoff_passes(self):
        self.add_receipt("cloud")
        self.add_receipt("ftmo")
        payload = handoff.audit(self.db, self.projects, now=self.now)
        self.assertTrue(payload["ready"])
        self.assertEqual({"complete": 2, "incomplete": 0, "invalid": 0, "missing": 0}, payload["counts"])
        self.assertTrue(payload["read_only"])

    def test_missing_project_receipt_is_reported_without_payload(self):
        self.add_receipt("cloud")
        payload = handoff.audit(self.db, self.projects, now=self.now)
        self.assertFalse(payload["ready"])
        ftmo = next(x for x in payload["projects"] if x["project_id"] == "ftmo")
        self.assertEqual("missing", ftmo["state"])
        self.assertEqual(["receipt"], ftmo["missing"])

    def test_required_handoff_facts_are_reason_codes_only(self):
        secret = "SUPER_SECRET_HANDOFF_TEXT"
        self.add_receipt(
            "cloud",
            action="",
            commit="not-a-commit",
            ci="",
            next_gate="",
            evidence={"private": secret},
        )
        self.add_receipt("ftmo")
        payload = handoff.audit(self.db, self.projects, now=self.now)
        cloud = next(x for x in payload["projects"] if x["project_id"] == "cloud")
        self.assertEqual("incomplete", cloud["state"])
        self.assertEqual(
            ["commit_or_pr", "change_summary", "live_status", "next_safe_task"],
            cloud["missing"],
        )
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret, serialized)
        for forbidden in ("action", "evidence_json", "next_gate", "commit_sha"):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_pr_reference_can_satisfy_commit_or_pr(self):
        self.add_receipt("cloud", commit="", evidence={"pr_number": 123, "proof": "GREEN"})
        self.add_receipt("ftmo")
        payload = handoff.audit(self.db, self.projects, now=self.now)
        cloud = next(x for x in payload["projects"] if x["project_id"] == "cloud")
        self.assertEqual("complete", cloud["state"])

    def test_empty_or_malformed_evidence_fails_closed(self):
        self.add_receipt("ftmo")
        with closing(sqlite3.connect(self.db)) as c:
            ts = (self.now - timedelta(minutes=1)).isoformat()
            c.execute(
                """INSERT INTO project_state_receipts(
                    project_id,action,commit_sha,ci_status,next_gate,observed_at,evidence_json
                ) VALUES(?,?,?,?,?,?,?)""",
                ("cloud", "Changed", "a"*40, "success", "Continue", ts, "{bad-json"),
            )
            c.commit()
        payload = handoff.audit(self.db, self.projects, now=self.now)
        cloud = next(x for x in payload["projects"] if x["project_id"] == "cloud")
        self.assertEqual("invalid", cloud["state"])
        self.assertIn("evidence_valid", cloud["missing"])

    def test_future_or_naive_observation_is_invalid(self):
        self.add_receipt("ftmo")
        self.add_receipt("cloud", observed_at=(self.now + timedelta(seconds=1)).isoformat())
        payload = handoff.audit(self.db, self.projects, now=self.now)
        cloud = next(x for x in payload["projects"] if x["project_id"] == "cloud")
        self.assertEqual("invalid", cloud["state"])
        self.assertIn("observed_at_valid", cloud["missing"])

    def test_only_latest_receipt_is_assessed(self):
        self.add_receipt("cloud", action="")
        self.add_receipt("cloud")
        self.add_receipt("ftmo")
        payload = handoff.audit(self.db, self.projects, now=self.now)
        cloud = next(x for x in payload["projects"] if x["project_id"] == "cloud")
        self.assertEqual("complete", cloud["state"])

    def test_database_is_not_modified(self):
        self.add_receipt("cloud")
        self.add_receipt("ftmo")
        before = (self.db.stat().st_size, self.db.stat().st_mtime_ns)
        handoff.audit(self.db, self.projects, now=self.now)
        after = (self.db.stat().st_size, self.db.stat().st_mtime_ns)
        self.assertEqual(before, after)

    def test_missing_schema_fails_closed(self):
        bad = self.root / "bad.db"
        with closing(sqlite3.connect(bad)) as c:
            c.execute("CREATE TABLE project_state_receipts(id INTEGER PRIMARY KEY, project_id TEXT)")
            c.commit()
        with self.assertRaises(RuntimeError):
            handoff.audit(bad, self.projects, now=self.now)

    def test_symlink_inputs_are_rejected(self):
        link = self.root / "history-link.db"
        link.symlink_to(self.db)
        with self.assertRaises(ValueError):
            handoff.audit(link, self.projects, now=self.now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
