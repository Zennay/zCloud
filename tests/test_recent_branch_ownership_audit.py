import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import zcloud_recent_branch_snapshot as collector  # noqa: E402
import zcloud_recent_branch_ownership_audit as audit  # noqa: E402

AUDIT_SCRIPT = ROOT / "scripts" / "zcloud_recent_branch_ownership_audit.py"
AS_OF = dt.datetime(2026, 10, 7, 3, 0, tzinfo=dt.timezone.utc)
MAIN = "a" * 40
RECENT = "b" * 40
OPEN = "c" * 40
STALE = "d" * 40


def graphql_page(nodes, *, has_next=False, cursor=None):
    return {
        "data": {
            "repository": {
                "refs": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                }
            }
        }
    }


def branch(name, sha, committed_at):
    return {
        "name": name,
        "target": {"oid": sha, "committedDate": committed_at},
    }


def compare_payload(*files, ahead=1, behind=0, merge_base=MAIN):
    return {
        "ahead_by": ahead,
        "behind_by": behind,
        "merge_base_commit": {"sha": merge_base},
        "files": [{"filename": path} for path in files],
    }


class RecentBranchCollectorTests(unittest.TestCase):
    def test_only_recent_unpr_branch_is_compared(self):
        calls = []

        def runner(args):
            calls.append(args)
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return [{"headRefName": "open-branch", "isCrossRepository": False}]
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(
                    [
                        branch("main", MAIN, "2026-10-07T02:55:00Z"),
                        branch("recent-owner", RECENT, "2026-10-07T02:30:00Z"),
                        branch("open-branch", OPEN, "2026-10-07T02:40:00Z"),
                        branch("stale", STALE, "2026-10-01T00:00:00Z"),
                    ]
                )
            if f"/compare/{MAIN}...{RECENT}" in joined:
                return compare_payload("server.py", "tests/x.py", ahead=2, behind=1)
            raise AssertionError(args)

        result = collector.collect_snapshot(
            "Zennay/zCloud", AS_OF, recent_hours=72, runner=runner
        )

        rows = {item["name"]: item for item in result["branches"]}
        self.assertIsNotNone(rows["recent-owner"]["compare"])
        self.assertTrue(rows["recent-owner"]["compare"]["file_list_complete"])
        self.assertIsNone(rows["main"]["compare"])
        self.assertIsNone(rows["open-branch"]["compare"])
        self.assertIsNone(rows["stale"]["compare"])
        compare_calls = [c for c in calls if "/compare/" in " ".join(c)]
        self.assertEqual(len(compare_calls), 1)
        compare_args = " ".join(compare_calls[0])
        self.assertIn("--jq", compare_args)
        self.assertIn("merge_base_commit", compare_args)
        for forbidden in ("message", "author", "committer", "verification"):
            self.assertNotIn(forbidden, compare_args)

    def test_cross_repository_pr_does_not_claim_same_named_local_branch(self):
        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return [{"headRefName": "fork-name", "isCrossRepository": True}]
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(
                    [
                        branch("main", MAIN, "2026-10-07T02:55:00Z"),
                        branch("fork-name", RECENT, "2026-10-07T02:30:00Z"),
                    ]
                )
            if f"/compare/{MAIN}...{RECENT}" in joined:
                return compare_payload("scripts/free.py")
            raise AssertionError(args)

        result = collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)
        row = next(item for item in result["branches"] if item["name"] == "fork-name")
        self.assertFalse(row["has_open_pr"])
        self.assertIsNotNone(row["compare"])

    def test_rename_owns_both_old_and_new_paths(self):
        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(
                    [
                        branch("main", MAIN, "2026-10-07T02:55:00Z"),
                        branch("rename-owner", RECENT, "2026-10-07T02:30:00Z"),
                    ]
                )
            if "/compare/" in joined:
                return {
                    "ahead_by": 1,
                    "behind_by": 0,
                    "merge_base_commit": {"sha": MAIN},
                    "files": [
                        {
                            "filename": "scripts/new_name.py",
                            "previous_filename": "scripts/old_name.py",
                        }
                    ],
                }
            raise AssertionError(args)

        result = collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)
        row = next(
            item for item in result["branches"] if item["name"] == "rename-owner"
        )
        self.assertEqual(
            row["compare"]["files"],
            ["scripts/new_name.py", "scripts/old_name.py"],
        )

    def test_exact_300_compare_files_is_marked_incomplete(self):
        files = [f"tests/f{i:03d}.py" for i in range(300)]

        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(
                    [
                        branch("main", MAIN, "2026-10-07T02:55:00Z"),
                        branch("large", RECENT, "2026-10-07T02:30:00Z"),
                    ]
                )
            if "/compare/" in joined:
                return compare_payload(*files)
            raise AssertionError(args)

        result = collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)
        row = next(item for item in result["branches"] if item["name"] == "large")
        self.assertFalse(row["compare"]["file_list_complete"])

    def test_open_pr_overflow_fails_closed(self):
        rows = [
            {"headRefName": f"branch-{i}", "isCrossRepository": False}
            for i in range(collector.MAX_OPEN_PRS + 1)
        ]

        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return rows
            raise AssertionError(args)

        with self.assertRaisesRegex(collector.CollectorError, "open_pr_bound_exceeded"):
            collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)

    def test_recent_compare_fanout_bound_fails_before_compare_calls(self):
        nodes = [branch("main", MAIN, "2026-10-07T02:55:00Z")]
        nodes.extend(
            branch(
                f"recent-{i:03d}",
                f"{i + 1000:040x}",
                "2026-10-07T02:30:00Z",
            )
            for i in range(collector.MAX_RECENT_COMPARE_BRANCHES + 1)
        )

        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(nodes)
            if "/compare/" in joined:
                self.fail("collector must fail before issuing compare calls")
            raise AssertionError(args)

        with self.assertRaisesRegex(
            collector.CollectorError, "recent_branch_compare_bound_exceeded"
        ):
            collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)

    def test_open_pr_snapshot_change_fails_closed(self):
        pr_calls = 0

        def runner(args):
            nonlocal pr_calls
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                pr_calls += 1
                if pr_calls == 1:
                    return []
                return [{"headRefName": "late-owner", "isCrossRepository": False}]
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(
                    [branch("main", MAIN, "2026-10-07T02:55:00Z")]
                )
            raise AssertionError(args)

        with self.assertRaisesRegex(
            collector.CollectorError, "open_pr_snapshot_changed"
        ):
            collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)

    def test_branch_ref_change_during_scan_fails_closed(self):
        graph_calls = 0

        def runner(args):
            nonlocal graph_calls
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                graph_calls += 1
                sha = MAIN if graph_calls == 1 else OPEN
                return graphql_page(
                    [branch("main", sha, "2026-10-07T02:55:00Z")]
                )
            raise AssertionError(args)

        with self.assertRaisesRegex(
            collector.CollectorError, "branch_snapshot_changed"
        ):
            collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)

    def test_branch_pagination_overflow_fails_closed(self):
        page = [
            branch(f"b{i}", f"{i:040x}", "2026-10-01T00:00:00Z")
            for i in range(collector.MAX_BRANCHES)
        ]

        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": MAIN}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                return graphql_page(page, has_next=True, cursor="more")
            raise AssertionError(args)

        with self.assertRaisesRegex(collector.CollectorError, "branch_bound_exceeded"):
            collector.collect_snapshot("Zennay/zCloud", AS_OF, 72, runner)


def snapshot_payload(branches):
    return {
        "schema_version": collector.SCHEMA_VERSION,
        "repository": "Zennay/zCloud",
        "main_sha": MAIN,
        "as_of": "2026-10-07T03:00:00Z",
        "recent_hours": 72,
        "branch_count": len(branches),
        "open_pr_head_count": sum(1 for item in branches if item["has_open_pr"]),
        "branches": branches,
    }


def snapshot_branch(
    name,
    sha,
    committed_at,
    *,
    has_open_pr=False,
    compare=None,
):
    return {
        "name": name,
        "sha": sha,
        "committed_at": committed_at,
        "has_open_pr": has_open_pr,
        "compare": compare,
    }


def normalized_compare(files, *, ahead=1, behind=0, complete=True):
    return {
        "ahead_by": ahead,
        "behind_by": behind,
        "merge_base_sha": MAIN,
        "files": files,
        "file_list_complete": complete,
    }


class RecentBranchAuditTests(unittest.TestCase):
    def test_recent_ahead_unpr_branch_conflicts_with_current_path(self):
        payload = snapshot_payload(
            [
                snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                snapshot_branch(
                    "worker/free",
                    RECENT,
                    "2026-10-07T02:30:00Z",
                    compare=normalized_compare(["scripts/shared.py", "tests/worker.py"], ahead=2),
                ),
            ]
        )
        parsed = audit.parse_snapshot(payload)
        report = audit.build_report(parsed, ("scripts/shared.py", "tests/current.py"))

        self.assertFalse(report["clear"])
        self.assertEqual(report["recent_unpr_owner_count"], 1)
        self.assertEqual(
            report["conflicts"],
            [{"path": "scripts/shared.py", "branches": [{"name": "worker/free", "sha": RECENT}]}],
        )

    def test_stale_open_pr_and_not_ahead_branches_are_not_owners(self):
        payload = snapshot_payload(
            [
                snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                snapshot_branch(
                    "open",
                    OPEN,
                    "2026-10-07T02:40:00Z",
                    has_open_pr=True,
                ),
                snapshot_branch("stale", STALE, "2026-10-01T00:00:00Z"),
                snapshot_branch(
                    "recent-no-ahead",
                    RECENT,
                    "2026-10-07T02:30:00Z",
                    compare=normalized_compare([], ahead=0, behind=2),
                ),
            ]
        )
        report = audit.build_report(audit.parse_snapshot(payload), ("server.py",))

        self.assertTrue(report["clear"])
        self.assertEqual(report["recent_unpr_owner_count"], 0)
        self.assertEqual(report["represented_by_open_pr_count"], 1)
        self.assertEqual(report["stale_branch_count"], 1)
        self.assertEqual(report["recent_not_ahead_count"], 1)

    def test_divergent_recent_branch_still_owns_unique_paths(self):
        payload = snapshot_payload(
            [
                snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                snapshot_branch(
                    "diverged",
                    RECENT,
                    "2026-10-07T02:30:00Z",
                    compare=normalized_compare(["server.py"], ahead=3, behind=4),
                ),
            ]
        )
        report = audit.build_report(audit.parse_snapshot(payload), ("server.py",))
        self.assertEqual(report["conflict_count"], 1)
        self.assertEqual(report["owners"][0]["ahead_by"], 3)
        self.assertEqual(report["owners"][0]["behind_by"], 4)

    def test_incomplete_recent_compare_fails_closed(self):
        payload = snapshot_payload(
            [
                snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                snapshot_branch(
                    "too-large",
                    RECENT,
                    "2026-10-07T02:30:00Z",
                    compare=normalized_compare(["server.py"], complete=False),
                ),
            ]
        )
        with self.assertRaisesRegex(
            audit.AuditError, "recent_branch_file_evidence_incomplete"
        ):
            audit.build_report(audit.parse_snapshot(payload), ("server.py",))

    def test_collector_must_not_attach_compare_to_stale_or_open_branch(self):
        for row in (
            snapshot_branch(
                "stale",
                STALE,
                "2026-10-01T00:00:00Z",
                compare=normalized_compare(["x.py"]),
            ),
            snapshot_branch(
                "open",
                OPEN,
                "2026-10-07T02:40:00Z",
                has_open_pr=True,
                compare=normalized_compare(["x.py"]),
            ),
        ):
            with self.subTest(name=row["name"]):
                payload = snapshot_payload(
                    [
                        snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                        row,
                    ]
                )
                parsed = audit.parse_snapshot(payload)
                with self.assertRaises(audit.AuditError):
                    audit.build_report(parsed, ("x.py",))

    def test_current_pr_head_mismatch_fails_closed(self):
        with self.assertRaisesRegex(audit.AuditError, "current_pr_head_mismatch"):
            audit.parse_current_pr(
                {
                    "headRefOid": OPEN,
                    "changedFiles": 1,
                    "files": [{"path": "server.py"}],
                },
                RECENT,
            )

    def test_current_pr_file_truncation_fails_closed(self):
        with self.assertRaisesRegex(
            audit.AuditError, "current_pr_file_evidence_incomplete"
        ):
            audit.parse_current_pr(
                {
                    "headRefOid": RECENT,
                    "changedFiles": 2,
                    "files": [{"path": "scripts/only-one.py"}],
                },
                RECENT,
            )

    def test_cli_distinguishes_conflict_from_bad_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot.json"
            current = root / "current.json"
            snapshot.write_text(
                json.dumps(
                    snapshot_payload(
                        [
                            snapshot_branch("main", MAIN, "2026-10-07T02:55:00Z"),
                            snapshot_branch(
                                "owner",
                                RECENT,
                                "2026-10-07T02:30:00Z",
                                compare=normalized_compare(["server.py"]),
                            ),
                        ]
                    )
                ),
                encoding="utf-8",
            )
            current.write_text(
                json.dumps(
                    {"headRefOid": RECENT, "changedFiles": 1, "files": [{"path": "server.py"}]}
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--snapshot",
                    str(snapshot),
                    "--current-pr-json",
                    str(current),
                    "--expected-current-head",
                    RECENT,
                    "--require-clear",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["conflict_count"], 1)

            current.write_text("{}", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--snapshot",
                    str(snapshot),
                    "--current-pr-json",
                    str(current),
                    "--expected-current-head",
                    RECENT,
                    "--require-clear",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("error", json.loads(proc.stdout))

    def test_symlink_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "snapshot.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "snapshot-link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(audit.AuditError, "input_symlink_rejected"):
                audit._read_json_file(link)


if __name__ == "__main__":
    unittest.main()
