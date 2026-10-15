"""Offline GitHub GET pagination and exact-main ownership snapshot tests."""
from __future__ import annotations

import copy
import unittest

from scripts import zcloud_deployops_pr_owner_snapshot_20261015 as s
from scripts.zcloud_deployops_pr_owner_collision_20261015 import InvalidInventory, assess

A = "a" * 40
B = "b" * 40


def detail(number, path="docs/a.md", *, previous=None):
    info = {
        "number": number,
        "state": "open",
        "changed_files": 1,
        "head": {"sha": B, "repo": {"full_name": "Zennay/zCloud"}},
        "base": {"sha": A, "repo": {"full_name": "Zennay/zCloud"}},
    }
    record = {"filename": path, "status": "modified"}
    if previous is not None:
        record.update(status="renamed", previous_filename=previous)
    return info, [record]


class FakeGitHub:
    def __init__(self, count=2):
        self.count = count
        self.calls = []
        self.main_calls = 0
        self.drift = False
        self.duplicate_page = False
        self.truncate_files = False
        self.details = {n: detail(n, path=f"docs/{n}.md")
                        for n in range(1, count + 1)}

    def __call__(self, path):
        self.calls.append(path)
        if path == "/branches/main":
            self.main_calls += 1
            return {"commit": {"sha": B if self.drift and self.main_calls > 1 else A}}
        if path.startswith("/pulls?"):
            page = int(path.rsplit("=", 1)[-1])
            start = (page - 1) * 100 + 1
            batch = [{"number": n} for n in range(start, min(start + 100, self.count + 1))]
            if self.duplicate_page and page > 1:
                return [{"number": 1}]
            return batch
        if path.startswith("/pulls/"):
            number = int(path.split("/")[2])
            metadata, files = self.details[number]
            if path.endswith("/files?per_page=100&page=1"):
                return [] if self.truncate_files else copy.deepcopy(files)
            return copy.deepcopy(metadata)
        raise AssertionError(path)


class ReadOnlyPRSnapshotTests(unittest.TestCase):
    def test_complete_snapshot_is_valid_and_does_not_mutate(self):
        api = FakeGitHub(2)
        snapshot = s.collect(api)
        self.assertEqual(2, len(snapshot["pulls"]))
        self.assertEqual(2, api.main_calls)
        self.assertEqual(A, snapshot["main_sha"])
        self.assertEqual(0, assess(snapshot)["collision_count"])
        self.assertEqual("Zennay/zCloud", snapshot["repo"])
        self.assertTrue(all(call.startswith(("/pulls", "/branches/main"))
                            for call in api.calls))

    def test_rename_both_paths_propagate_to_collision_proof(self):
        api = FakeGitHub(2)
        api.details[1] = detail(1, "docs/new.md", previous="docs/old.md")
        api.details[2] = detail(2, "docs/old.md")
        result = assess(s.collect(api))
        self.assertEqual([{"path": "docs/old.md", "prs": [1, 2]}],
                         result["collisions"])

    def test_second_page_is_required_and_complete(self):
        api = FakeGitHub(101)
        result = s.collect(api)
        self.assertEqual(101, len(result["pulls"]))
        self.assertIn("/pulls?state=open&per_page=100&page=2", api.calls)
        self.assertEqual(2, api.main_calls)

    def test_duplicate_pr_across_pages_is_denied(self):
        api = FakeGitHub(101)
        api.duplicate_page = True
        with self.assertRaisesRegex(InvalidInventory, "duplicate_or_invalid_pr_page"):
            s.collect(api)

    def test_pagination_budget_exhaustion_denied(self):
        api = FakeGitHub(101)
        with self.assertRaisesRegex(InvalidInventory, "incomplete_pr_pagination"):
            s.collect(api, max_pages=1)

    def test_invalid_page_budget_denied(self):
        for budget in (0, 11, True, "2", -1):
            with self.subTest(budget=budget), self.assertRaises(InvalidInventory):
                s.collect(FakeGitHub(), max_pages=budget)

    def test_missing_files_or_changed_count_denied(self):
        api = FakeGitHub()
        api.truncate_files = True
        with self.assertRaisesRegex(InvalidInventory, "incomplete_pr_files_page"):
            s.collect(api)
        api = FakeGitHub()
        api.details[1][0]["changed_files"] = True
        with self.assertRaisesRegex(InvalidInventory, "incomplete_or_oversized_pr_files"):
            s.collect(api)
        api.details[1][0]["changed_files"] = 100
        with self.assertRaisesRegex(InvalidInventory, "incomplete_or_oversized_pr_files"):
            s.collect(api)

    def test_current_main_drift_denied(self):
        api = FakeGitHub()
        api.drift = True
        with self.assertRaisesRegex(InvalidInventory, "main_changed_during_snapshot"):
            s.collect(api)

    def test_wrong_pr_identity_denied(self):
        api = FakeGitHub()
        api.details[1][0]["number"] = 999
        with self.assertRaisesRegex(InvalidInventory, "wrong_pr_detail"):
            s.collect(api)

    def test_unexpected_repo_and_missing_rename_source_denied(self):
        api = FakeGitHub()
        api.details[1][0]["head"]["repo"]["full_name"] = "attacker/repo"
        with self.assertRaisesRegex(InvalidInventory, "untrusted_or_foreign_pr"):
            s.collect(api)
        api = FakeGitHub()
        api.details[1][1][0]["status"] = "renamed"
        with self.assertRaisesRegex(InvalidInventory, "unsafe_or_duplicate_file_path"):
            s.collect(api)

    def test_missing_invalid_tokens_denied_before_transport(self):
        for token in ("", "bad\nheader", " " * 1025):
            with self.subTest(token_len=len(token)), self.assertRaises(
                InvalidInventory
            ):
                s.github_reader(token)

    def test_no_release_or_runtime_mutation_codepaths(self):
        from pathlib import Path
        code = Path(s.__file__).read_text(encoding="utf-8")
        self.assertIn('method="GET"', code)
        for forbidden in ("method=\"POST\"", "method=\"PUT\"", "method=\"DELETE\"",
                          "unlink(", "systemctl ", "git push", "sqlite3.connect", "GITHUB_TOKEN="):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
