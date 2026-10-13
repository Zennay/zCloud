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

    def test_measured_live_scale_1423_branches_15_pages_completes_without_authority(self):
        # Measured via GitHub repository branch pagination on 13 October.
        all_branches = [branch("main", S)] + [
            branch(f"archive/lane-{i:04d}", A) for i in range(1422)
        ]
        pages = [all_branches[i:i + 100] for i in range(0, len(all_branches), 100)]
        evidence = mod.collect(Fixture(branches=pages, pulls=[[]]), S)
        self.assertTrue(evidence["inventory_complete"])
        self.assertEqual(evidence["branch_count"], 1423)
        self.assertEqual(evidence["branch_pages"], 15)
        self.assertEqual(len(evidence["pr_less"]), 1422)
        self.assertFalse(evidence["admission_authorized"])
        self.assertFalse(evidence["deployment_authorized"])

    def test_missing_or_moving_main_in_branch_pages_is_denied(self):
        for branches in ([branch("other", A)],
                         [branch("main", B), branch("other", A)]):
            with self.subTest(branches=branches):
                with self.assertRaisesRegex(mod.InventoryDenied, "listed_main_mismatch"):
                    mod.collect(Fixture(branches=[branches], pulls=[[]]), S)

    def test_malformed_overfull_api_page_denied(self):
        overfull = [branch("main", S)] + [
            branch(f"branch-{i}", A) for i in range(100)]
        with self.assertRaisesRegex(mod.InventoryDenied, "page_size_exceeded"):
            mod.collect(Fixture(branches=[overfull], pulls=[[]]), S)

    def test_inventory_exceeding_total_branch_cap_is_denied(self):
        many = [branch("main", S)] + [
            branch(f"lane-{i:04d}", A) for i in range(300)
        ]
        pages = [many[i:i + 100] for i in range(0, len(many), 100)]
        with patch.object(mod, "MAX_ENTRIES", 300):
            with self.assertRaisesRegex(mod.InventoryDenied, "entry_cap"):
                mod.collect(Fixture(branches=pages, pulls=[[]]), S)

    def test_full_page_without_next_cursor_never_claims_completeness(self):
        full = [branch("main", S)] + [branch(f"branch-{i}", A) for i in range(99)]
        with self.assertRaisesRegex(mod.InventoryDenied, "full_page_without_cursor"):
            mod.collect(Fixture(branches=[full], pulls=[[]]), S)
        with self.assertRaisesRegex(mod.InventoryDenied, "full_page_without_cursor"):
            mod.collect(Fixture(branches=[[branch("main", S)]],
                                        pulls=[[pr(f"owned-{i}", A) for i in range(100)]]), S)

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


class OverlapTests(unittest.TestCase):
    def setUp(self):
        # The second page contains the PR-less branch whose compare files
        # may overlap, while 'main' is represented by a PR head.
        self.snapshot = mod.collect(Fixture(), S)

    def source(self, *, files=None, status="ahead", base=S, total=1, ahead=1,
               changed_inventory=False):
        source = Fixture(branches=[[branch("main", S)],
                                   [branch("new", B if changed_inventory else A)]])
        def fetch(kind, page):
            if kind.startswith("compare:"):
                self.assertEqual(kind, f"compare:{S}...{A}")
                return {
                    "base_commit": {"sha": base},
                    "head_commit": {"sha": A},
                    "commits": [{"sha": A}] * total if 0 <= total <= 250 else [],
                    "status": status,
                    "total_commits": total, "ahead_by": ahead,
                    "files": files if files is not None else [
                        {"filename": "scripts/worker.py", "status": "modified"}
                    ]
                }, None
            return source(kind, page)
        return fetch

    def test_later_page_overlap_denied_as_ownership_observation(self):
        output = mod.compare_owned_paths(
            self.source(), self.snapshot, ["scripts/"])
        self.assertTrue(output["overlap_found"])
        self.assertEqual(output["overlaps"], [
            {"branch": "new", "changed_path": "scripts/worker.py",
             "owned_path": "scripts/"}])
        self.assertTrue(output["paths_compared"])
        self.assertFalse(output["admission_authorized"])
        self.assertFalse(output["deployment_authorized"])

    def test_unrelated_path_is_no_overlap_observation_not_authorization(self):
        output = mod.compare_owned_paths(
            self.source(), self.snapshot, ["public/app.js"])
        self.assertFalse(output["overlap_found"])
        self.assertEqual(output["compared_branches"], 1)
        self.assertFalse(output["admission_authorized"])

    def test_renamed_previous_path_also_checked(self):
        result = mod.compare_owned_paths(
            self.source(files=[{"filename": "docs/new.md",
                                "previous_filename": "public/old.js",
                                "status": "renamed"}]),
            self.snapshot, ["public/"])
        self.assertTrue(result["overlap_found"])
        self.assertEqual(result["overlaps"][0]["changed_path"], "public/old.js")

    def test_missing_owned_paths_and_untrusted_inventory_deny(self):
        with self.assertRaisesRegex(mod.InventoryDenied, "missing_owned_paths"):
            mod.compare_owned_paths(self.source(), self.snapshot, [])
        bad = dict(self.snapshot, admission_authorized=True)
        with self.assertRaisesRegex(mod.InventoryDenied, "untrusted_inventory"):
            mod.compare_owned_paths(self.source(), bad, ["scripts/"])

    def test_invalid_or_traversal_file_path_denied(self):
        for file in ("../secret", "/absolute/path", "a//b", "a\\b", "a\nb"):
            with self.subTest(file=file):
                with self.assertRaisesRegex(mod.InventoryDenied, "invalid_changed_path"):
                    mod.compare_owned_paths(self.source(files=[{"filename": file, "status": "modified"}]),
                                            self.snapshot, ["scripts/"])

    def test_incomplete_truncated_or_malformed_compare_denied(self):
        cases = [
            ({"base": B}, "compare_base_mismatch"),
            ({"total": 251}, "compare_commit_cap_or_invalid"),
            ({"ahead": -1}, "compare_commit_cap_or_invalid"),
            ({"status": "unknown"}, "invalid_compare_status"),
            ({"files": [{"filename": f"f{i}"} for i in range(300)]},
             "compare_files_incomplete"),
            ({"files": [{"filename": "rename", "status": "renamed"}]},
             "invalid_changed_path"),
        ]
        for kwargs, reason in cases:
            with self.subTest(case=reason):
                with self.assertRaisesRegex(mod.InventoryDenied, reason):
                    mod.compare_owned_paths(self.source(**kwargs),
                                            self.snapshot, ["scripts/"])

    def test_many_overlapping_paths_fail_closed_before_unbounded_evidence(self):
        files = [
            {"filename": f"src/nested/file-{i}.py", "status": "modified"}
            for i in range(140)
        ]
        with self.assertRaisesRegex(mod.InventoryDenied, "overlap_evidence_cap"):
            mod.compare_owned_paths(self.source(files=files), self.snapshot,
                                    ["src/", "src/nested/"])

    def test_unknown_or_missing_file_status_is_denied(self):
        for status in (None, "moved", "", False, 42):
            with self.subTest(status=status):
                with self.assertRaisesRegex(mod.InventoryDenied, "unknown_compare_file_status"):
                    mod.compare_owned_paths(
                        self.source(files=[{"filename": "src/x.py", "status": status}]),
                        self.snapshot, ["src/"])

    def test_valid_removed_and_added_files_both_checked(self):
        evidence = mod.compare_owned_paths(
            self.source(files=[
                {"filename": "src/old.py", "status": "removed"},
                {"filename": "src/new.py", "status": "added"}
            ]), self.snapshot, ["src/"])
        self.assertEqual(len(evidence["overlaps"]), 2)
        self.assertFalse(evidence["admission_authorized"])

    def test_post_compare_branch_head_race_denied(self):
        with self.assertRaisesRegex(mod.InventoryDenied, "post_compare_inventory_drift"):
            mod.compare_owned_paths(self.source(changed_inventory=True),
                                    self.snapshot, ["scripts/"])

    def test_transport_immutable_compare_never_uses_named_branch(self):
        seen = []
        class Response:
            status = 200
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b'{"files":[]}'
        class Opener:
            def open(self, req, timeout):
                seen.append((req.get_method(), req.full_url))
                return Response()
        with patch.object(mod.urllib.request, "build_opener", return_value=Opener()):
            payload, next_page = mod.github_fetcher("")(f"compare:{S}...{A}", 0)
        self.assertEqual((payload, next_page), ({"files": []}, None))
        self.assertEqual(seen, [("GET", f"https://api.github.com/repos/Zennay/zCloud/compare/{S}...{A}")])


    def test_compare_wrong_head_or_missing_head_rejected(self):
        correct = self.source()
        for bad in ({"sha": B}, {}, None):
            with self.subTest(head=bad):
                def fetch(kind, page):
                    payload, next_page = correct(kind, page)
                    if kind.startswith("compare:"):
                        payload["head_commit"] = bad
                    return payload, next_page
                with self.assertRaises(mod.InventoryDenied):
                    mod.compare_owned_paths(fetch, self.snapshot, ["scripts/"])

    def test_compare_partial_or_spoofed_commit_history_rejected(self):
        correct = self.source()
        for bogus in ([], [{"sha": B}], [{"sha": "invalid"}], None,
                      [{"sha": A}, {"sha": A}]):
            with self.subTest(commits=bogus):
                def fetch(kind, page):
                    payload, next_page = correct(kind, page)
                    if kind.startswith("compare:"):
                        payload["commits"] = bogus
                    return payload, next_page
                with self.assertRaisesRegex(mod.InventoryDenied, "compare_commit_history_incomplete"):
                    mod.compare_owned_paths(fetch, self.snapshot, ["scripts/"])

    def test_compare_link_pagination_denied_without_second_get(self):
        class Response:
            status = 200
            headers = {"Link": '<https://api.github.com/repos/Zennay/zCloud/compare/a...b?page=2>; rel="next"'}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n):
                return b'{"files":[]}'
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request.full_url)
                return Response()
        with patch.object(mod.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(mod.InventoryDenied, "paginated_compare_incomplete"):
                mod.github_fetcher("")(f"compare:{S}...{A}", 0)
        self.assertEqual(len(calls), 1)

    def test_rejects_symbolic_or_spoofed_compare_refs(self):
        for value in ("compare:main...branch", f"compare:{S}...main",
                      f"compare:{S}...{A}/other"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(mod.InventoryDenied, "invalid_compare_identity"):
                    mod.github_fetcher("")(value, 0)


class BatchOverlapTests(unittest.TestCase):
    def test_large_inventory_can_be_examined_in_disjoint_denied_batches(self):
        rows = [branch("main", S)] + [
            branch(f"lane-{i:03d}", A) for i in range(180)
        ]
        pages = [rows[i:i + 100] for i in range(0, len(rows), 100)]
        initial = mod.collect(Fixture(branches=pages, pulls=[[]]), S)
        self.assertEqual(len(initial["pr_less"]), 180)
        fixture = Fixture(branches=pages, pulls=[[]])
        compared = []
        def fetch(kind, page):
            if kind.startswith("compare:"):
                compared.append(kind)
                return {"base_commit": {"sha": S}, "head_commit": {"sha": A},
                        "commits": [{"sha": A}], "status": "ahead",
                        "total_commits": 1, "ahead_by": 1,
                        "files": [{"filename": "public/index.html", "status": "modified"}]}, None
            return fixture(kind, page)
        with self.assertRaisesRegex(mod.InventoryDenied, "branch_compare_cap"):
            mod.compare_owned_paths(fetch, initial, ["public/"])
        self.assertEqual(compared, [], "over-cap unbatched path must not fetch compares")
        first = mod.compare_owned_paths(fetch, initial, ["public/"],
                                        batch_offset=0, batch_limit=80)
        self.assertFalse(first["coverage_complete"])
        self.assertEqual(first["uncompared_branches"], 100)
        self.assertEqual(first["compared_branches"], 80)
        self.assertTrue(first["overlap_found"])
        self.assertFalse(first["admission_authorized"])
        last = mod.compare_owned_paths(fetch, initial, ["public/"],
                                       batch_offset=80, batch_limit=100)
        self.assertFalse(last["coverage_complete"])
        self.assertEqual(last["compared_branches"], 100)
        self.assertEqual(last["uncompared_branches"], 80)
        self.assertFalse(last["deployment_authorized"])
        self.assertEqual(len(compared), 180)

    def test_invalid_batch_ranges_deny_without_compare_requests(self):
        snap = mod.collect(Fixture(), S)
        for options in ({"batch_offset": -1, "batch_limit": 1},
                        {"batch_offset": True, "batch_limit": 1},
                        {"batch_offset": 1, "batch_limit": 1},
                        {"batch_offset": 0, "batch_limit": 0},
                        {"batch_offset": 0, "batch_limit": 151},
                        {"batch_offset": 1},
                        {"batch_offset": 0, "batch_limit": False}):
            with self.subTest(options=options):
                with self.assertRaises(mod.InventoryDenied):
                    mod.compare_owned_paths(
                        lambda *_: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
                        snap, ["public/"], **options)

    def test_single_branch_batch_is_complete_but_still_not_approved(self):
        snap = mod.collect(Fixture(), S)
        fixture = Fixture()
        def fetch(kind, page):
            if kind.startswith("compare:"):
                return {"base_commit": {"sha": S}, "head_commit": {"sha": A},
                        "commits": [{"sha": A}], "status": "ahead",
                        "total_commits": 1, "ahead_by": 1,
                        "files": []}, None
            return fixture(kind, page)
        result = mod.compare_owned_paths(fetch, snap, ["public/"],
                                         batch_offset=0, batch_limit=1)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["uncompared_branches"], 0)
        self.assertFalse(result["admission_authorized"])


class CLIOverlapTests(unittest.TestCase):
    def _fixture(self, kind, page):
        if kind.startswith("compare:"):
            return {"base_commit": {"sha": S}, "head_commit": {"sha": A},
                    "commits": [{"sha": A}], "status": "ahead",
                    "total_commits": 1, "ahead_by": 1,
                    "files": [{"filename": "public/app.js", "status": "modified"}]}, None
        return Fixture()(kind, page)

    def test_cli_can_diagnose_immutable_paths_without_ever_authorizing(self):
        # One shared fixture across all listing calls; separate compare response.
        base = Fixture()
        def fetch(kind, page):
            if kind.startswith("compare:"):
                return {"base_commit": {"sha": S}, "head_commit": {"sha": A},
                        "commits": [{"sha": A}], "status": "ahead",
                        "total_commits": 1, "ahead_by": 1,
                        "files": [{"filename": "public/app.js", "status": "modified"}]}, None
            return base(kind, page)
        with patch.object(mod, "github_fetcher", return_value=fetch):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                code = mod.main([S, "--owned-path", "public/"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertTrue(data["paths_compared"])
        self.assertTrue(data["overlap_found"])
        self.assertFalse(data["admission_authorized"])
        self.assertFalse(data["deployment_authorized"])

    def test_cli_accepts_explicit_bounded_batch_options(self):
        base = Fixture()
        def fetch(kind, page):
            if kind.startswith("compare:"):
                return {"base_commit": {"sha": S}, "head_commit": {"sha": A},
                        "commits": [{"sha": A}], "status": "ahead",
                        "total_commits": 1, "ahead_by": 1, "files": []}, None
            return base(kind, page)
        with patch.object(mod, "github_fetcher", return_value=fetch):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                result = mod.main([S, "--owned-path", "public/",
                                   "--branch-offset", "0", "--branch-limit", "1"])
        self.assertEqual(result, 0)
        content = json.loads(output.getvalue())
        self.assertTrue(content["coverage_complete"])
        self.assertEqual(content["batch_offset"], 0)
        self.assertFalse(content["admission_authorized"])

    def test_invalid_batch_cli_flags_deny_before_network(self):
        invalid_args = [
            [S, "--owned-path", "public/", "--branch-offset", "0"],
            [S, "--branch-offset", "0", "--branch-limit", "1"],
            [S, "--owned-path", "public/", "--branch-offset", "01", "--branch-limit", "1"],
            [S, "--owned-path", "public/", "--branch-offset", "-1", "--branch-limit", "1"],
            [S, "--owned-path", "../escape", "--branch-offset", "0", "--branch-limit", "1"],
            [S, "--owned-path", "public/", "--branch-limit", "1",
             "--branch-limit", "2", "--branch-offset", "0"]
        ]
        with patch.object(mod, "github_fetcher", side_effect=AssertionError("network invoked")):
            for args in invalid_args:
                with self.subTest(args=args):
                    with patch("sys.stdout", new_callable=io.StringIO) as output:
                        self.assertEqual(mod.main(args), 2)
                    self.assertFalse(json.loads(output.getvalue())["deployment_authorized"])

    def test_cli_rejects_extra_undefined_arguments_without_network(self):
        with patch.object(mod, "github_fetcher", side_effect=AssertionError("network invoked")):
            for args in ([], [S, "--owned-path"], [S, "--invalid", "docs/"],
                         [S, "--owned-path", "docs/", "orphan"]):
                with self.subTest(args=args):
                    with patch("sys.stdout", new_callable=io.StringIO) as output:
                        self.assertEqual(mod.main(args), 2)
                    self.assertFalse(json.loads(output.getvalue())["admission_authorized"])


class HostedProofWorkflowTests(unittest.TestCase):
    def test_ci_is_readonly_and_pins_true_pr_head_not_merge_ref(self):
        workflow = (ROOT / '.github/workflows/zcloud-deploy-prless-inventory-hosted-20261013-w1.yml').read_text()
        event_sha = 'github.event.pull_request.head.sha || github.sha'
        self.assertGreaterEqual(workflow.count(event_sha), 2)
        self.assertNotIn('ref: 
    unittest.main()
 + '{' + '{ github.sha }}', workflow)
        self.assertIn('EXPECTED_HEAD_SHA:', workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"', workflow)
        self.assertIn('runs-on: ubuntu-latest', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('contents: read', workflow)
        self.assertNotIn('runs-on: self-hosted', workflow)
        self.assertNotIn('\n  workflow_dispatch:', workflow)

if __name__ == "__main__":
    unittest.main()
