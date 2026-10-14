#!/usr/bin/env python3
"""Offline fail-closed checks for the autonomous PR integration engine."""
import importlib.util
import pathlib
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts/zcloud_autonomous_pr_integrator.py"
spec = importlib.util.spec_from_file_location("automerge_under_test", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
A = "a" * 40
B = "b" * 40
ENV = {"GITHUB_REPOSITORY": "Zennay/zCloud", "GITHUB_REF": "refs/heads/main",
       "GITHUB_SHA": B, "GITHUB_EVENT_NAME": "workflow_dispatch",
       "GITHUB_TOKEN": "unit-test-token", "ZCLOUD_AUTOMERGE_ENABLED": "true",
       "GITHUB_ACTOR": "Zennay", "GITHUB_TRIGGERING_ACTOR": "Zennay"}


def pr(docs=True):
    return {"number": 21, "state": "open", "draft": False,
            "mergeable": True, "mergeable_state": "clean", "title": "Useful change",
            "labels": [{"name": mod.LABEL}],
            "head": {"sha": A, "repo": {"full_name": mod.REPO}},
            "base": {"sha": B, "ref": "main", "repo": {"full_name": mod.REPO}}}


def files(docs=True):
    return [{"filename": "docs/worker-integration.md" if docs else "scripts/new_engine.py",
             "status": "added"}]


def checks():
    return [{"head_sha": A, "name": "critical-runner-flows", "status": "completed", "conclusion": "success"}]


class DecisionTest(unittest.TestCase):
    def test_docs_green(self):
        self.assertEqual(mod.decide(pr(), files(), checks(), [], B), "eligible")

    def test_development_source_independent_from_production_release(self):
        # A production blocker must not freeze separate development merges.
        # Unsafe core runtime paths remain forbidden by safe_path().
        self.assertEqual(mod.decide(pr(False), files(False), checks(), [], B), "eligible")
        ok = [{"context": "other-required-check", "state": "success"}]
        self.assertEqual(mod.decide(pr(False), files(False), checks(), ok, B), "eligible")

    def test_nonopted_pr_does_not_merge(self):
        p = pr()
        p["labels"] = []
        self.assertEqual(mod.decide(p, files(), checks(), [], B), "no_explicit_label")

    def test_draft_and_unmergeable(self):
        p = pr()
        p["draft"] = True
        self.assertEqual(mod.decide(p, files(), checks(), [], B), "not_review_ready")
        p = pr()
        p["mergeable_state"] = "blocked"
        self.assertEqual(mod.decide(p, files(), checks(), [], B), "not_cleanly_mergeable")

    def test_fork_or_drift_denied(self):
        p = pr()
        p["head"]["repo"]["full_name"] = "unknown/fork"
        self.assertEqual(mod.decide(p, files(), checks(), [], B), "foreign_or_empty_head")
        p = pr()
        p["base"]["sha"] = A
        self.assertEqual(mod.decide(p, files(), checks(), [], B), "base_drift")

    def test_incomplete_checks(self):
        self.assertEqual(mod.decide(pr(), files(), [], [], B), "missing_or_unbounded_checks")
        x = checks()
        x[0]["status"] = "in_progress"
        self.assertEqual(mod.decide(pr(), files(), x, [], B), "required_smoke_not_green")
        x = checks()
        x[0]["head_sha"] = B
        self.assertEqual(mod.decide(pr(), files(), x, [], B), "unbound_check")

    def test_optional_queued_vps_cpu_must_not_block_green_hosted_smoke(self):
        optional = {"head_sha": A, "name": "cpu-snapshot",
                    "status": "queued", "conclusion": None}
        self.assertEqual(mod.decide(pr(), files(), checks() + [optional], [], B),
                         "eligible")
        unknown = dict(optional, name="unknown-required-or-optional")
        self.assertEqual(mod.decide(pr(), files(), checks() + [unknown], [], B),
                         "check_not_green")
        smoke = checks()
        smoke[0]["conclusion"] = "failure"
        self.assertEqual(mod.decide(pr(), files(), smoke + [optional], [], B),
                         "required_smoke_not_green")
        self.assertEqual(mod.decide(pr(), files(), [optional], [], B),
                         "required_smoke_missing")

    def test_known_optional_queued_vps_can_explain_unstable_state(self):
        p = pr()
        p["mergeable_state"] = "unstable"
        optional = {"head_sha": A, "name": "cpu-snapshot",
                    "status": "queued", "conclusion": None}
        self.assertEqual(mod.decide(p, files(), checks(), [], B),
                         "unstable_without_known_optional")
        self.assertEqual(mod.decide(p, files(), checks() + [optional], [], B),
                         "eligible")
        bad = dict(optional, name="unknown-queued")
        self.assertEqual(mod.decide(p, files(), checks() + [bad], [], B),
                         "check_not_green")
        p["mergeable_state"] = "blocked"
        self.assertEqual(mod.decide(p, files(), checks() + [optional], [], B),
                         "not_cleanly_mergeable")
        p["mergeable_state"] = "unstable"
        p["mergeable"] = False
        self.assertEqual(mod.decide(p, files(), checks() + [optional], [], B),
                         "not_cleanly_mergeable")

    def test_failed_status_denied(self):
        s = [{"context": "unit-test", "state": "failure"}]
        self.assertEqual(mod.decide(pr(), files(), checks(), s, B), "status_not_green")

    def test_no_special_runtime_paths(self):
        for path in (".github/workflows/unsafe.yml", "server.py", "public/app.js",
                     "projects.json", "scripts/../server.py", "/docs/x.md",
                     "docs//x.md", "docs\\x.md", "experimental/main.py"):
            with self.subTest(path=path):
                self.assertFalse(mod.safe_path(path))

    def test_files_pagination_or_duplicates(self):
        self.assertEqual(mod.decide(pr(), files()*100, checks(), [], B),
                         "incomplete_file_inventory")
        self.assertEqual(mod.decide(pr(), files()*2, checks(), [], B),
                         "unsafe_or_duplicate_path")

    def test_deleted_or_renamed_denied(self):
        for status in ("removed", "renamed"):
            x = files()
            x[0]["status"] = status
            self.assertEqual(mod.decide(pr(), x, checks(), [], B), "unsupported_file_change")

    def test_untrusted_event_and_apply_gate(self):
        for env_change in ({"GITHUB_EVENT_NAME": "pull_request"},
                           {"GITHUB_REF": "refs/heads/branch"},
                           {"GITHUB_REPOSITORY": "another/repo"},
                           {"GITHUB_SHA": A}):
            env = {**ENV, **env_change}
            if env_change.get("GITHUB_SHA") == A:
                continue
            with self.subTest(env_change=env_change), self.assertRaises(mod.Refused):
                mod.main([], env, self.transport)
        with self.assertRaises(mod.Refused):
            mod.main(["--apply"], {**ENV, "ZCLOUD_AUTOMERGE_ENABLED": "false"}, self.transport)

    @staticmethod
    def transport(token, method, path, data=None):
        if path == "/branches/main":
            return {"commit": {"sha": B}}
        if path.startswith("/pulls?"):
            return [pr()]
        if path == "/pulls/21":
            return pr()
        if path.endswith("/files?per_page=100"):
            return files()
        if path.endswith("/check-runs?per_page=100"):
            return {"total_count": 1, "check_runs": checks()}
        if path.endswith("/statuses?per_page=100"):
            return []
        if path.endswith("/merge"):
            return {"merged": True, "sha": "c" * 40}
        raise AssertionError("unexpected API " + path)

    def test_dry_run_never_merges(self):
        calls = []
        def transport(token, method, path, data=None):
            calls.append((method, path))
            return self.transport(token, method, path, data)
        self.assertEqual(mod.main([], ENV, transport), 0)
        self.assertFalse(any(method == "PUT" for method, _ in calls))

    def test_apply_one_sha_bound_merge(self):
        calls = []
        def transport(token, method, path, data=None):
            calls.append((method, path, data))
            return self.transport(token, method, path, data)
        self.assertEqual(mod.main(["--apply"], ENV, transport), 0)
        writes = [x for x in calls if x[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2]["sha"], A)
        self.assertTrue(writes[0][2]["commit_title"].endswith(" [skip ci]"))

    def test_untrusted_multiline_title_cannot_change_commit_directives(self):
        def transport(token, method, path, data=None):
            if path == "/pulls/21":
                malicious = pr()
                malicious["title"] = "ok\\nproduction restart"
                return malicious
            if method == "PUT":
                self.assertNotIn("\\n", data["commit_title"])
                self.assertTrue(data["commit_title"].endswith(" [skip ci]"))
            return self.transport(token, method, path, data)
        self.assertEqual(mod.main(["--apply"], ENV, transport), 0)

    def test_main_moves_before_merge(self):
        branch_calls = 0
        def transport(token, method, path, data=None):
            nonlocal branch_calls
            if path == "/branches/main":
                branch_calls += 1
                return {"commit": {"sha": B if branch_calls == 1 else A}}
            if method == "PUT":
                self.fail("must not merge stale main")
            return self.transport(token, method, path, data)
        self.assertEqual(mod.main(["--apply"], ENV, transport), 0)

    def test_paginated_pr_inventory_reaches_older_opted_in_items(self):
        paths = []
        def transport(token, method, path, data=None):
            paths.append(path)
            if path.endswith("page=1"):
                return [{"number": i, "labels": []} for i in range(1, 101)]
            if path.endswith("page=2"):
                return [{"number": 1001, "labels": [{"name": mod.LABEL}]}]
            self.fail("unexpected page: " + path)
        result = mod.list_open_prs(transport, "test-token")
        self.assertEqual(len(result), 101)
        self.assertEqual(result[-1]["number"], 1001)
        self.assertEqual(len(paths), 2)

    def test_pagination_fails_closed_on_duplicate_or_oversized_backlog(self):
        def duplicate(token, method, path, data=None):
            if path.endswith("page=1"):
                return [{"number": i} for i in range(1, 101)]
            return [{"number": 1}]
        with self.assertRaises(mod.Refused):
            mod.list_open_prs(duplicate, "test-token")
        def endless(token, method, path, data=None):
            page = int(path.split("page=")[-1])
            return [{"number": 100 * (page - 1) + i} for i in range(1, 101)]
        with self.assertRaises(mod.Refused):
            mod.list_open_prs(endless, "test-token")
        def malformed(token, method, path, data=None):
            return [{"number": True}]
        with self.assertRaises(mod.Refused):
            mod.list_open_prs(malformed, "test-token")

    def test_only_original_and_triggering_owner_may_apply(self):
        for key in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"):
            with self.subTest(key=key), self.assertRaises(mod.Refused):
                mod.main(["--apply"], {**ENV, key: "untrusted"}, self.transport)

    def test_missing_token_denied(self):
        with self.assertRaises(mod.Refused):
            mod.main([], {**ENV, "GITHUB_TOKEN": ""}, self.transport)


if __name__ == "__main__":
    unittest.main()
