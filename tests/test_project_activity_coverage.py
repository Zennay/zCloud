import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_project_activity_coverage as coverage


class ProjectActivityCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-activity-coverage-")
        root = Path(self.tmp.name)
        self.db = root / "history.db"
        self.contracts = root / "project-contracts.json"
        self.contracts.write_text(json.dumps({
            "schema_version": 1,
            "projects": {
                "cloud": {},
                "ftmo": {},
                "ulab": {},
                "zguard": {},
            },
        }), encoding="utf-8")
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executescript(
                """
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    project_id TEXT
                );
                CREATE TABLE events(
                    id TEXT PRIMARY KEY,
                    ts TEXT,
                    project TEXT,
                    kind TEXT,
                    title TEXT,
                    detail TEXT
                );
                """
            )
            connection.commit()
        self.now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_runner_generic_stale_and_never_seen_sources(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(
                "INSERT INTO runner_events(ts,event,project_id) VALUES(?,?,?)",
                ((self.now - timedelta(hours=1)).isoformat(), "generation-finished", "cloud"),
            )
            connection.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                ("e1", (self.now - timedelta(hours=2)).isoformat(), "ulab", "dogfood", "secret title", "secret detail"),
            )
            connection.execute(
                "INSERT INTO runner_events(ts,event,project_id) VALUES(?,?,?)",
                ((self.now - timedelta(hours=200)).isoformat(), "generation-finished", "ftmo"),
            )
            connection.commit()
        payload = coverage.report(self.db, self.contracts, 168, self.now)
        by_project = {row["project_id"]: row for row in payload["projects"]}
        self.assertEqual("recent", by_project["cloud"]["state"])
        self.assertEqual(["runner_events"], [x["source"] for x in by_project["cloud"]["sources"]])
        self.assertEqual("recent", by_project["ulab"]["state"])
        self.assertEqual(["events"], [x["source"] for x in by_project["ulab"]["sources"]])
        self.assertEqual("inactive_or_stale", by_project["ftmo"]["state"])
        self.assertEqual("never_seen", by_project["zguard"]["state"])
        self.assertEqual(1, payload["summary"]["never_seen"])
        serialized = json.dumps(payload)
        self.assertNotIn("secret title", serialized)
        self.assertNotIn("secret detail", serialized)

    def test_database_remains_byte_and_mtime_stable(self):
        before = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        coverage.report(self.db, self.contracts, 168, self.now)
        after = (self.db.read_bytes(), self.db.stat().st_mtime_ns, self.db.stat().st_size)
        self.assertEqual(before, after)

    def test_symlink_inputs_are_rejected(self):
        db_link = Path(self.tmp.name) / "history-link.db"
        db_link.symlink_to(self.db)
        with self.assertRaisesRegex(ValueError, "symlink database"):
            coverage.report(db_link, self.contracts, 168, self.now)
        contracts_link = Path(self.tmp.name) / "contracts-link.json"
        contracts_link.symlink_to(self.contracts)
        with self.assertRaisesRegex(ValueError, "symlink project-contract"):
            coverage.report(self.db, contracts_link, 168, self.now)

    def test_missing_runner_event_schema_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        with closing(sqlite3.connect(broken)) as connection:
            connection.execute("CREATE TABLE runner_events(id INTEGER PRIMARY KEY)")
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "project_id"):
            coverage.report(broken, self.contracts, 168, self.now)

    def test_contract_and_stale_bounds_fail_closed(self):
        empty = Path(self.tmp.name) / "empty-contracts.json"
        empty.write_text('{"schema_version":1,"projects":{}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no projects"):
            coverage.report(self.db, empty, 168, self.now)
        with self.assertRaises(ValueError):
            coverage.report(self.db, self.contracts, 0.5, self.now)
        with self.assertRaises(ValueError):
            coverage.report(self.db, self.contracts, 2161, self.now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
