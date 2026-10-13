"""Offline, deterministic denied-first tests for #1164 GitHub branch pagination."""
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/zcloud_deploy_prless_inventory_20261013_w1.py"
spec = importlib.util.spec_from_file_location("deploy_inventory_w1", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

S = "a" * 40
A = "b" * 40
B = "c" * 40


def branch(name, sha=A):
    return {"name": name, "commit": {"sha": sha}}


def pr(name, sha=A, repo="Zennay/zCloud"):
    return {"head": {"ref": name, "sha": sha, "repo": {"full_name": repo}}}


class Fixture:
    def __init__(self, branches=None, pulls=None, main=None):
        self.branches = branches if branches is not None else [[branch("main", S)], [branch("new")]]
        self.pulls = pulls if pulls is not None else [[pr("main", S)], []]
        self.main = main or [S, S]
        self.calls = {"branches": 0, "pulls": 0, "main": 0}

    def __call__(self, kind, page):
        count = self.calls[kind]
        self.calls[kind] += 1
        if kind == "main":
            return self.main[min(count, len(self.main) - 1)], None
        fixture = self.branches if kind == "branches" else self.pulls
        # Each full scan uses the same two pages by default.
        idx = (page - 1)
        if idx >= len(fixture):
            raise AssertionError("unexpected requested page")
        return fixture[idx], page + 1 if idx + 1 < len(fixture) else None


class PaginationTests(unittest.TestCase):
    def test_accepts_complete_two_page_snapshot_but_never_authorizes(self):
        result = mod.collect(Fixture(), S)
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(result["pr_less"], [{"branch": "new", "commit_sha": A}])
        self.assertEqual(result["branch_pages"], 2)
        self.assertFalse(result["admission_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["paths_compared"])

    def test_matches_open_pr_heads_without_counting_as_prless(self):
        f = Fixture(branches=[[branch("main", S), branch("owned")]],
                    pulls=[[pr("owned")]])
        self.assertEqual(mod.collect(f, S)["pr_less"], [])

    def test_external_fork_ref_does_not_own_local_branch(self):
        f = Fixture(branches=[[branch("main", S), branch("owned")]],
                    pulls=[[pr("owned", repo="elsewhere/fork")]])
        self.assertEqual(mod.collect(f, S)["pr_less"][0]["branch"], "owned")

    def test_pr_sha_mismatch_denied(self):
        f = Fixture(branches=[[branch("main", S), branch("owned", A)]],
                    pulls=[[pr("owned", B)]])
        with self.assertRaisesRegex(mod.InventoryDenied, "pr_head_drift"):
            mod.collect(f, S)

    def test_moving_main_before_and_after_denied(self):
        with self.assertRaisesRegex(mod.InventoryDenied, "main_drift_before"):
            mod.collect(Fixture(main=[B]), S)
        with self.assertRaisesRegex(mod.InventoryDenied, "main_drift_after"):
            mod.collect(Fixture(main=[S, B]), S)

    def test_second_scan_branch_drift_denied(self):
        f = Fixture(branches=[[branch("main", S), branch("new", A)]])
        original = f.__call__
        def moving(kind, page):
            result = original(kind, page)
            if kind == "branches" and f.calls["branches"] > 1:
                return [branch("main", S), branch("new", B)], None
            return result
        with self.assertRaisesRegex(mod.InventoryDenied, "branch_inventory_drift"):
            mod.collect(moving, S)

    def test_second_scan_pr_drift_denied(self):
        f = Fixture(branches=[[branch("main", S), branch("new")]],
                    pulls=[[pr("new")]])
        original = f.__call__
        def moving(kind, page):
            result = original(kind, page)
            if kind == "pulls" and f.calls["pulls"] > 1:
                return [pr("new", B)], None
            return result
        with self.assertRaisesRegex(mod.InventoryDenied, "pr_inventory_drift"):
            mod.collect(moving, S)

    def test_all_partial_page_states_deny(self):
        bad_next = [0, 1, 3, True, "next", -4]
        for next_page in bad_next:
            with self.subTest(next_page=next_page):
                def fetch(kind, page):
                    if kind == "main":
                        return S, None
                    return [branch("main", S)] if kind == "branches" else [], next_page
                with self.assertRaises(mod.InventoryDenied):
                    mod.collect(fetch, S)

    def test_empty_intermediate_page_denied(self):
        def fetch(kind, page):
            if kind == "main":
                return S, None
            if kind == "branches":
                return [], 2
            return [], None
        with self.assertRaisesRegex(mod.InventoryDenied, "empty_middle_page"):
            mod.collect(fetch, S)

    def test_duplicate_branch_or_missing_head_denied(self):
        for branches, pulls in (
            ([[branch("same"), branch("same")]], [[]]),
            ([[branch("main", S)]], [{"head": {}}]),
        ):
            with self.subTest(branches=branches, pulls=pulls):
                with self.assertRaises(mod.InventoryDenied):
                    mod.collect(Fixture(branches=branches, pulls=pulls), S)

    def test_branch_limit_and_page_limit_denied(self):
        with patch.object(mod, "MAX_ENTRIES", 1):
            with self.assertRaisesRegex(mod.InventoryDenied, "entry_cap"):
                mod.collect(Fixture(), S)
        with patch.object(mod, "MAX_PAGES", 1):
            with self.assertRaisesRegex(mod.InventoryDenied, "page_cap"):
                mod.collect(Fixture(), S)

    def test_malformed_sha_and_names_denied(self):
        cases = [
            ([[branch("main", S), branch("../../secret")]], [[]]),
            ([[branch("main", "not-a-sha")]], [[]]),
            ([[branch("main", S)]], [[pr("x", "bad")]]),
        ]
        for branches, pulls in cases:
            with self.subTest(branches=branches, pulls=pulls):
                with self.assertRaises(mod.InventoryDenied):
                    mod.collect(Fixture(branches=branches, pulls=pulls), S)

    def test_denied_cli_never_claims_admission(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(mod.main(["not-sha"]), 2)
        result = json.loads(output.getvalue())
        self.assertFalse(result["inventory_complete"])
        self.assertFalse(result["admission_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertNotIn("pr_less", result)

    def test_transport_rejects_foreign_next_link(self):
        class Response:
            status = 200
            headers = {"Link": '<https://evil.example/repos/Zennay/zCloud/branches?per_page=100&page=2>; rel="next"'}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b"[]"
        with patch.object(mod.urllib.request, "build_opener", return_value=type("Opener", (), {"open": lambda self, *a, **kw: Response()})()):
            with self.assertRaisesRegex(mod.InventoryDenied, "link_origin_or_query_mismatch"):
                mod.github_fetcher("")("branches", 1)

    def test_transport_accepts_only_contiguous_expected_next_page(self):
        class Response:
            status = 200
            headers = {"Link": '<https://api.github.com/repos/Zennay/zCloud/branches?per_page=100&page=2>; rel="next"'}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b"[]"
        with patch.object(mod.urllib.request, "build_opener", return_value=type("Opener", (), {"open": lambda self, *a, **kw: Response()})()):
            self.assertEqual(mod.github_fetcher("")("branches", 1), ([], 2))

    def test_authenticated_redirects_denied_before_followup_request(self):
        handler = mod._DenyRedirect()
        with self.assertRaisesRegex(mod.InventoryDenied, "redirect_denied"):
            handler.redirect_request(None, None, 302, "moved", {}, "https://example.org/steal")

    def test_api_request_method_is_get_and_has_bounded_timeout(self):
        seen = []
        class Response:
            status = 200
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b"[]"
        class Opener:
            def open(self, request, timeout):
                seen.append((request.get_method(), request.full_url, timeout))
                return Response()
        with patch.object(mod.urllib.request, "build_opener", return_value=Opener()):
            self.assertEqual(mod.github_fetcher("")("branches", 1), ([], None))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "GET")
        self.assertEqual(seen[0][2], 12)
        self.assertTrue(seen[0][1].startswith("https://api.github.com/repos/Zennay/zCloud/branches?"))


if __name__ == "__main__":
    unittest.main()
