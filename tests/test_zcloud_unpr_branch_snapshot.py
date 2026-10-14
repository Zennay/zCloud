import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import zcloud_unpr_branch_snapshot as snap


NOW = dt.datetime(2026, 10, 7, 7, 55, tzinfo=dt.timezone.utc)


class UnprBranchSnapshotTests(unittest.TestCase):
    def test_open_pr_heads_ignore_cross_repo_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prs.json"
            path.write_text(
                json.dumps(
                    [
                        {"headRefName": "feature/local", "isCrossRepository": False},
                        {"headRefName": "feature/fork-name", "isCrossRepository": True},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual({"feature/local"}, snap._load_open_pr_heads(path))

    def test_open_pr_inventory_fails_closed_at_501_rows(self):
        rows = [
            {"headRefName": f"feature/x-{idx}", "isCrossRepository": False}
            for idx in range(501)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prs.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaisesRegex(
                snap.SnapshotError, "open_pr_snapshot_incomplete_or_oversized"
            ):
                snap._load_open_pr_heads(path)

    @mock.patch.object(snap, "_branch_changed_files")
    @mock.patch.object(snap, "_origin_branches")
    def test_build_snapshot_excludes_main_and_open_pr_heads(self, origin, changed):
        origin.return_value = ["feature/open", "feature/unpr", "main"]
        changed.return_value = ["scripts/a.py"]
        result = snap.build_snapshot(
            Path("/repo"),
            {"feature/open"},
            now=NOW,
        )
        self.assertEqual(
            [
                {
                    "name": "feature/unpr",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["scripts/a.py"],
                }
            ],
            result["branches"],
        )
        self.assertTrue(result["inventory_complete"])
        changed.assert_called_once_with(
            Path("/repo"), "origin", "main", "feature/unpr"
        )

    @mock.patch.object(snap, "_run_git")
    def test_origin_branch_inventory_requires_main_and_bounds_unmerged_refs(self, run_git):
        run_git.return_value = "not-a-sha\n"
        with self.assertRaisesRegex(snap.SnapshotError, "base_branch_missing"):
            snap._origin_branches(Path("/repo"), "origin")

        run_git.side_effect = [
            "0123456789abcdef0123456789abcdef01234567\n",
            "".join(f"feature/{idx}\n" for idx in range(snap.MAX_BRANCHES + 1)),
        ]
        with self.assertRaisesRegex(
            snap.SnapshotError, "remote_branch_inventory_too_large"
        ):
            snap._origin_branches(Path("/repo"), "origin")

    @mock.patch.object(snap, "_run_git")
    def test_origin_branch_inventory_asks_git_for_only_unmerged_refs(self, run_git):
        run_git.side_effect = [
            "0123456789abcdef0123456789abcdef01234567\n",
            "feature/z\nfeature/a\n",
        ]
        self.assertEqual(
            ["feature/a", "feature/z"],
            snap._origin_branches(Path("/repo"), "origin"),
        )
        self.assertIn(
            "--no-merged=refs/remotes/origin/main",
            run_git.call_args_list[1].args,
        )

    @mock.patch.object(snap, "_run_git")
    @mock.patch.object(snap, "_merge_base")
    def test_changed_files_are_sorted_and_bounded(self, merge_base, run_git):
        merge_base.return_value = "0123456789abcdef0123456789abcdef01234567"
        run_git.return_value = "tests/z.py\nscripts/a.py\n"
        self.assertEqual(
            ["scripts/a.py", "tests/z.py"],
            snap._branch_changed_files(
                Path("/repo"), "origin", "main", "feature/test"
            ),
        )

    @mock.patch.object(snap, "_run_git")
    @mock.patch.object(snap, "_merge_base")
    def test_orphan_branch_owns_its_full_tree(self, merge_base, run_git):
        merge_base.return_value = None
        run_git.return_value = "docs/site.md\nassets/logo.svg\n"
        self.assertEqual(
            ["assets/logo.svg", "docs/site.md"],
            snap._branch_changed_files(
                Path("/repo"), "origin", "main", "orphan/site"
            ),
        )
        self.assertEqual("ls-tree", run_git.call_args.args[1])

    def test_write_json_rejects_symlink_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "snapshot.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(snap.SnapshotError, "output_not_regular"):
                snap._write_json(link, {"ok": True})

    @mock.patch.object(snap, "_branch_changed_files")
    @mock.patch.object(snap, "_origin_branches")
    def test_snapshot_shape_is_accepted_by_overlap_audit(self, origin, changed):
        from scripts import zcloud_unpr_branch_overlap_audit as audit

        origin.return_value = ["feature/unpr", "main"]
        changed.return_value = ["scripts/shared.py"]
        payload = snap.build_snapshot(Path("/repo"), set(), now=NOW)
        result = audit.audit(payload, ["scripts/shared.py"], NOW)
        self.assertEqual("overlap", result["status"])
        self.assertEqual("feature/unpr", result["overlaps"][0]["branch"])


if __name__ == "__main__":
    unittest.main()
