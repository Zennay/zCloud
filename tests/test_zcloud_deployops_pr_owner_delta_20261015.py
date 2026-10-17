"""Offline, deny-first regressions for bounded PR snapshot comparisons."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import zcloud_deployops_pr_owner_delta_20261015 as delta
from scripts.zcloud_deployops_pr_owner_collision_20261015 import InvalidInventory

A = "a" * 40
B = "b" * 40
C = "c" * 40


def pr(n, *paths, head=B, base=A, status="modified"):
    return {
        "number": n, "state": "open", "head_sha": head, "base_sha": base,
        "head_repo": "Zennay/zCloud", "base_repo": "Zennay/zCloud",
        "changed_files": len(paths),
        "files": [{"filename": path, "status": status} for path in paths],
    }


def inventory(*pulls, main=A):
    return {"repo": "Zennay/zCloud", "main_sha": main,
            "pulls": list(pulls)}


class PRSnapshotDeltaTests(unittest.TestCase):
    def test_identical_inputs_produce_no_changes_but_never_authority(self):
        before = inventory(pr(1, "docs/a.md"), pr(2, "docs/b.md"))
        result = delta.compare_snapshots(before, copy.deepcopy(before))
        self.assertEqual("unchanged", result["status"])
        self.assertFalse(result["inventory_changed"])
        self.assertEqual(0, result["added_prs"]["count"])
        self.assertEqual(result["previous_snapshot_sha256"],
                         result["current_snapshot_sha256"])
        self.assertTrue(result["operator_review_required"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["includes_pr_less_branches"])

    def test_different_snapshot_order_is_not_reported_as_identical_evidence(self):
        before = inventory(pr(1, "docs/a.md"), pr(2, "docs/b.md"))
        after = inventory(pr(2, "docs/b.md"), pr(1, "docs/a.md"))
        result = delta.compare_snapshots(before, after)
        self.assertEqual("changed", result["status"])
        self.assertEqual(0, result["changed_prs"]["count"])
        self.assertEqual(0, result["changed_path_ownership"]["count"])
        self.assertNotEqual(result["previous_snapshot_sha256"],
                            result["current_snapshot_sha256"])

    def test_new_removed_and_changed_prs_exact_paths_are_reported(self):
        before = inventory(pr(1, "docs/a.md"), pr(2, "docs/c.md"))
        current = inventory(pr(1, "docs/a.md", head=C),
                            pr(3, "docs/a.md"))
        result = delta.compare_snapshots(before, current)
        self.assertEqual("changed", result["status"])
        self.assertEqual([3], result["added_prs"]["items"])
        self.assertEqual([2], result["removed_prs"]["items"])
        self.assertEqual([1], result["changed_prs"]["items"])
        self.assertEqual(["docs/a.md"],
                         result["new_exact_collision_paths"]["items"])
        self.assertEqual(1, result["current_exact_collision_count"])
        self.assertIn("docs/c.md", result["changed_path_ownership"]["items"])

    def test_conflict_owner_replacement_not_hidden_when_counts_same(self):
        before = inventory(pr(1, "docs/a.md"), pr(2, "docs/a.md"))
        after = inventory(pr(1, "docs/a.md"), pr(3, "docs/a.md"))
        result = delta.compare_snapshots(before, after)
        self.assertEqual(result["previous_exact_collision_count"],
                         result["current_exact_collision_count"])
        self.assertTrue(result["inventory_changed"])
        self.assertEqual(["docs/a.md"],
                         result["changed_path_ownership"]["items"])
        self.assertEqual(0, result["new_exact_collision_paths"]["count"])

    def test_renamed_source_is_counted_as_owner_even_if_absent_at_destination(self):
        moved = pr(2, "docs/new.md", status="renamed")
        moved["files"][0]["previous_filename"] = "docs/old.md"
        baseline = inventory(pr(1, "docs/old.md"))
        current = inventory(pr(1, "docs/old.md"), moved)
        result = delta.compare_snapshots(baseline, current)
        self.assertEqual(["docs/old.md"],
                         result["new_exact_collision_paths"]["items"])
        self.assertIn("docs/new.md", result["changed_path_ownership"]["items"])

    def test_file_directory_conflict_owner_changes_are_visible(self):
        before = inventory(pr(1, "docs/ops"), pr(2, "docs/ops/readme.md"))
        after = inventory(pr(1, "docs/ops"), pr(3, "docs/ops/readme.md"))
        result = delta.compare_snapshots(before, after)
        self.assertEqual(1, result["previous_hierarchy_collision_count"])
        self.assertEqual(1, result["current_hierarchy_collision_count"])
        self.assertEqual(["docs/ops -> docs/ops/readme.md"],
                         result["changed_hierarchy_conflicts"]["items"])

    def test_similar_string_prefix_does_not_form_false_hierarchy_edge(self):
        before = inventory(pr(1, "docs/app"), pr(2, "docs/app-two/readme"))
        after = inventory(pr(1, "docs/app"), pr(3, "docs/app-two/readme"))
        result = delta.compare_snapshots(before, after)
        self.assertEqual(0, result["changed_hierarchy_conflicts"]["count"])
        self.assertEqual(0, result["current_hierarchy_collision_count"])

    def test_changed_main_revision_refused_even_if_prs_identical(self):
        before = inventory(pr(1, "docs/a.md"))
        after = inventory(pr(1, "docs/a.md"), main=C)
        with self.assertRaisesRegex(InvalidInventory,
                                    "different_main_revisions_no_safe_comparison"):
            delta.compare_snapshots(before, after)

    def test_incomplete_or_foreign_or_duplicate_snapshot_refused(self):
        before = inventory(pr(1, "docs/a.md"))
        bad_cases = [
            {"repo": "Zennay/zCloud", "main_sha": A, "pulls": []},
            inventory(pr(1, "docs/a.md"), pr(1, "docs/a.md")),
            {"repo": "evil/fork", "main_sha": A, "pulls": [pr(1, "docs/a.md")]},
            inventory(pr(2, "docs/../../not-ok")),
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad), self.assertRaises(InvalidInventory):
                delta.compare_snapshots(before, bad)

    def test_changed_report_is_bounded_without_silently_losing_counts(self):
        before = inventory(pr(1, "docs/original"))
        after = inventory(pr(1, "docs/original"), *[
            pr(i, f"docs/{i}.md") for i in range(2, 95)
        ])
        result = delta.compare_snapshots(before, after)
        self.assertEqual(93, result["added_prs"]["count"])
        self.assertEqual(50, len(result["added_prs"]["items"]))
        self.assertTrue(result["added_prs"]["truncated"])
        self.assertEqual(93, result["changed_path_ownership"]["count"])

    def test_cli_exact_equal_differs_from_changed_and_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            current = root / "current.json"
            data = inventory(pr(1, "docs/a.md"))
            baseline.write_text(json.dumps(data), encoding="utf-8")
            current.write_text(json.dumps(data), encoding="utf-8")
            original_bytes = baseline.read_bytes()
            def call():
                out = io.StringIO()
                with redirect_stdout(out):
                    code = delta.main(["--baseline", str(baseline),
                                       "--snapshot", str(current)])
                return code, json.loads(out.getvalue())
            code, result = call()
            self.assertEqual(0, code)
            self.assertEqual("unchanged", result["status"])
            current.write_text(json.dumps(inventory(
                pr(1, "docs/a.md"), pr(2, "docs/a.md"))), encoding="utf-8")
            code, result = call()
            self.assertEqual(1, code)
            self.assertEqual("changed", result["status"])
            self.assertFalse(result["release_authorized"])
            current.write_text('{"repo":"Zennay/zCloud","repo":"evil"}',
                               encoding="utf-8")
            code, result = call()
            self.assertEqual(2, code)
            self.assertEqual("refused", result["status"])
            self.assertFalse(result["deployment_authorized"])
            self.assertEqual(original_bytes, baseline.read_bytes())

    def test_cli_denies_symlinked_parent_and_never_follows_to_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            baseline = real / "baseline.json"
            baseline.write_text(json.dumps(inventory(pr(1, "docs/a.md"))),
                                encoding="utf-8")
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            captured = io.StringIO()
            with redirect_stdout(captured):
                code = delta.main(["--baseline", str(alias / "baseline.json"),
                                   "--snapshot", str(baseline)])
            self.assertEqual(2, code)
            self.assertEqual("refused", json.loads(captured.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
