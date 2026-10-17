"""Regression tests for fair read-only zCloud Activity Feed selection (#1518)."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_activity_fair_page", ROOT / "scripts" / "zcloud_activity_fair_page.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
BASE = "2026-10-17T04:00:00+00:00"


def add(db, pid, eid, second, *, ts=None):
    db.execute(
        "INSERT INTO events(id,ts,project,kind,title,detail) VALUES(?,?,?,?,?,?)",
        (eid, ts or f"2026-10-17T04:00:{second:02d}+00:00",
         pid, "commit", f"safe {eid}", f"detail {eid}"),
    )


class FairActivityPageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE events(id TEXT PRIMARY KEY, ts TEXT, project TEXT, "
            "kind TEXT, title TEXT, detail TEXT)"
        )

    def tearDown(self):
        self.db.close()

    def test_quiet_project_cannot_be_starved_by_80_newer_noisy_events(self):
        # Old SQL: ORDER BY ts DESC LIMIT 80, drops the quiet project.
        for i in range(101):
            add(self.db, "noisy", f"noisy-{i:03}", i % 60)
        add(self.db, "quiet", "quiet-event", 0,
            ts="2026-10-16T19:00:00+00:00")
        first = mod.fair_page(self.db, ["quiet", "noisy"])
        self.assertEqual(80, len(first["events"]))
        self.assertIn("quiet-event", [x["id"] for x in first["events"]])
        self.assertEqual(1, sum(x["project"] == "quiet" for x in first["events"]))
        self.assertEqual("not_synchronized",
                         first["coverage"]["github_pr_branch_ci"])
        self.assertFalse(first["coverage"]["feed_complete"])

    def test_multi_page_keyset_never_duplicates_or_skips_unreturned_rows(self):
        for i in range(90):
            add(self.db, "a", f"a-{i:03d}", i % 60)
        for i in range(13):
            add(self.db, "b", f"b-{i:03d}", i % 60)
        visited = []
        cursor = None
        for _ in range(10):
            p = mod.fair_page(self.db, ["b", "a"], page_size=17, cursor=cursor)
            visited.extend(x["id"] for x in p["events"])
            cursor = p["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(103, len(visited))
        self.assertEqual(103, len(set(visited)))
        self.assertEqual(90, sum(i.startswith("a-") for i in visited))
        self.assertEqual(13, sum(i.startswith("b-") for i in visited))

    def test_determinism_independent_of_registry_order(self):
        for project in ("a", "b", "c"):
            for i in range(3):
                add(self.db, project, f"{project}{i}", i)
        left = mod.fair_page(self.db, ["a", "b", "c"], page_size=6)
        right = mod.fair_page(self.db, ["c", "b", "a"], page_size=6)
        self.assertEqual(left, right)

    def test_project_isolation_and_absent_project(self):
        add(self.db, "internal", "secret-id", 59)
        add(self.db, "public", "visible-id", 1)
        answer = mod.fair_page(self.db, ["public", "missing"])
        self.assertEqual(["visible-id"], [row["id"] for row in answer["events"]])
        self.assertEqual(2, answer["coverage"]["projects_considered"])

    def test_empty_registry_or_invalid_page_or_duplicate_scope_fails(self):
        for args in (
            {"projects": []},
            {"projects": ["a", "a"]},
            {"projects": ["a", "x' OR 1=1 --"]},
            {"projects": ["A"]},
            {"projects": ["a"], "page_size": 0},
            {"projects": ["a"], "page_size": 81},
            {"projects": ["a"], "page_size": True},
            {"projects": ["a"] * 33},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mod.fair_page(self.db, **args)

    def test_refuse_foreign_cursor_and_unvalidated_cursor_value(self):
        for cur in (
            {"foreign": {"ts": BASE, "id": "e"}},
            {"a": {"ts": BASE, "id": ""}},
            {"a": {"ts": "yesterday", "id": "a"}},
            {"a": {"ts": BASE, "id": "e", "ignore": 1}},
            {"a": None},
        ):
            with self.subTest(cursor=cur), self.assertRaises(ValueError):
                mod.fair_page(self.db, ["a"], cursor=cur)

    def test_bad_stored_timestamp_is_explicit_failure_not_empty_feed(self):
        for bad in ("garbage", "2026-10-17T04:00:00",
                    "2026-10-17T06:00:00+02:00",
                    "2026-10-17T04:00:00Z"):
            with self.subTest(ts=bad):
                self.db.execute("DELETE FROM events")
                add(self.db, "a", "bad", 0, ts=bad)
                with self.assertRaises(ValueError):
                    mod.fair_page(self.db, ["a"])

    def test_no_events_is_not_false_github_coverage(self):
        p = mod.fair_page(self.db, ["a", "b"])
        self.assertEqual([], p["events"])
        self.assertIsNone(p["next_cursor"])
        self.assertFalse(p["coverage"]["feed_complete"])

    def test_file_backed_read_only_select_does_not_mutate_db(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.db"
            with sqlite3.connect(path) as db:
                db.execute(
                    "CREATE TABLE events(id TEXT PRIMARY KEY,ts TEXT,project TEXT,"
                    "kind TEXT,title TEXT,detail TEXT)"
                )
                add(db, "a", "one", 1)
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                db.execute("PRAGMA query_only=ON")
                result = mod.fair_page(db, ["a"])
                self.assertEqual("one", result["events"][0]["id"])
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM events")
            finally:
                db.close()
            self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))


if __name__ == "__main__":
    unittest.main()
