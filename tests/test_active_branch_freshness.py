from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from scripts import zcloud_active_branch_freshness as freshness


NOW = datetime(2026, 10, 6, 11, 30, tzinfo=timezone.utc)


class ActiveBranchFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.projects = self.root / "projects.json"
        self.projects.write_text(
            json.dumps(
                [
                    {"id": "cloud", "repo_url": "https://github.com/Zennay/zCloud", "status": "active"},
                    {"id": "supa", "repo_url": "https://github.com/Zennay/Supa", "status": "active"},
                    {"id": "local", "status": "active"},
                ]
            ),
            encoding="utf-8",
        )
        self.db = self.root / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE task_claims(
                    project_id TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '',
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(project_id,claim_key)
                )"""
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _claim(self, project: str, key: str, branch: str | None, *, active: bool = True, metadata=None) -> None:
        lease = NOW + timedelta(minutes=5) if active else NOW - timedelta(seconds=1)
        payload = {"branch": branch} if metadata is None and branch is not None else (metadata or {})
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO task_claims(
                    project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    project,
                    key,
                    "owner",
                    f"{project}::w1",
                    (NOW - timedelta(minutes=2)).isoformat(),
                    (NOW - timedelta(seconds=10)).isoformat(),
                    lease.isoformat(),
                    json.dumps(payload),
                ),
            )

    def test_classifies_current_stale_missing_untracked_and_unknown(self) -> None:
        self._claim("cloud", "current", "feature/current")
        self._claim("supa", "stale", "feature/stale")
        self._claim("cloud", "missing", "feature/missing")
        self._claim("supa", "untracked", None)
        self._claim("local", "unknown", "feature/local")

        def compare(repo: str, branch: str) -> dict:
            if branch == "feature/current":
                return {"status": "current", "ahead_by": 2, "behind_by": 0}
            if branch == "feature/stale":
                return {"status": "stale", "ahead_by": 1, "behind_by": 4}
            if branch == "feature/missing":
                return {"status": "missing", "ahead_by": None, "behind_by": None}
            raise AssertionError((repo, branch))

        payload = freshness.build_report(self.db, self.projects, now=NOW, compare_reader=compare)
        by_branch = {item["branch"]: item for item in payload["branches"]}

        self.assertEqual("current", by_branch["feature/current"]["status"])
        self.assertFalse(by_branch["feature/current"]["warning"])
        self.assertEqual(0, by_branch["feature/current"]["behind_by"])
        self.assertEqual("stale", by_branch["feature/stale"]["status"])
        self.assertTrue(by_branch["feature/stale"]["warning"])
        self.assertEqual(4, by_branch["feature/stale"]["behind_by"])
        self.assertEqual("missing", by_branch["feature/missing"]["status"])
        self.assertTrue(by_branch["feature/missing"]["warning"])
        self.assertEqual("untracked", by_branch[None]["status"])
        local = next(item for item in payload["branches"] if item["project_id"] == "local")
        self.assertEqual("unknown", local["status"])
        self.assertEqual("canonical_repo_unavailable", local["reason"])
        self.assertEqual(3, payload["warning_count"])

    def test_expired_claims_are_ignored(self) -> None:
        self._claim("cloud", "old", "feature/old", active=False)
        payload = freshness.build_report(
            self.db,
            self.projects,
            now=NOW,
            compare_reader=lambda repo, branch: (_ for _ in ()).throw(AssertionError("must not query GitHub")),
        )
        self.assertEqual([], payload["branches"])
        self.assertEqual(0, payload["warning_count"])

    def test_duplicate_branch_claims_are_aggregated(self) -> None:
        self._claim("cloud", "a", "feature/shared")
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO task_claims(
                    project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    "cloud",
                    "b",
                    "owner-b",
                    "cloud::w2",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    (NOW + timedelta(minutes=5)).isoformat(),
                    json.dumps({"branch": "feature/shared"}),
                ),
            )

        payload = freshness.build_report(
            self.db,
            self.projects,
            now=NOW,
            compare_reader=lambda repo, branch: {"status": "current", "ahead_by": 1, "behind_by": 0},
        )
        self.assertEqual(1, len(payload["branches"]))
        self.assertEqual(2, payload["branches"][0]["worker_count"])

    def test_malformed_claim_metadata_is_warning_without_leaking_raw_metadata(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO task_claims(
                    project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    "cloud",
                    "broken",
                    "secret-owner",
                    "cloud::w1",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    (NOW + timedelta(minutes=5)).isoformat(),
                    '{"secret":"unterminated"',
                ),
            )
        payload = freshness.build_report(self.db, self.projects, now=NOW)
        item = payload["branches"][0]
        self.assertEqual("unknown", item["status"])
        self.assertEqual("malformed_claim_metadata", item["reason"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("secret-owner", serialized)
        self.assertNotIn("unterminated", serialized)

    def test_github_compare_uses_sha_compare_and_encoded_branch(self) -> None:
        calls = []
        main_sha = "a" * 40
        branch_sha = "b" * 40

        def runner(command, **kwargs):
            calls.append(command)
            path = command[-1]
            if path == "repos/Zennay/zCloud/branches/main":
                payload = {"commit": {"sha": main_sha}}
            elif path == "repos/Zennay/zCloud/branches/feature%2Fwork":
                payload = {"commit": {"sha": branch_sha}}
            elif path == f"repos/Zennay/zCloud/compare/{main_sha}...{branch_sha}":
                payload = {"ahead_by": 3, "behind_by": 2}
            else:
                raise AssertionError(path)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        result = freshness.github_compare("Zennay/zCloud", "feature/work", runner=runner)
        self.assertEqual("stale", result["status"])
        self.assertEqual(2, result["behind_by"])
        self.assertEqual(3, result["ahead_by"])
        self.assertEqual(3, len(calls))

    def test_missing_remote_branch_is_distinct_from_repo_unavailable(self) -> None:
        main_sha = "a" * 40

        def missing_runner(command, **kwargs):
            path = command[-1]
            if path.endswith("/branches/main"):
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"commit": {"sha": main_sha}}), stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="gh: Branch not found (HTTP 404)")

        self.assertEqual(
            "missing",
            freshness.github_compare("Zennay/zCloud", "gone", runner=missing_runner)["status"],
        )

        def unavailable_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="network unavailable")

        with self.assertRaisesRegex(freshness.GitHubReadError, "unavailable"):
            freshness.github_compare("Zennay/zCloud", "work", runner=unavailable_runner)

    def test_symlink_database_is_rejected(self) -> None:
        link = self.root / "history-link.db"
        try:
            link.symlink_to(self.db)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            freshness.build_report(link, self.projects, now=NOW)


if __name__ == "__main__":
    unittest.main()
