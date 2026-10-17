"""Offline GitHub GET pagination and exact-main ownership snapshot tests."""
from __future__ import annotations

import copy
from email.message import Message
import unittest
from unittest.mock import patch
from urllib.error import URLError

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
        self.drift_head = False
        self.drift_head_at_completion = False
        self.drift_pr_list = False
        self.boolean_metadata_on_read = None
        self.list_pass = 0
        self.detail_reads = {}
        self.details = {n: detail(n, path=f"docs/{n}.md")
                        for n in range(1, count + 1)}

    def __call__(self, path):
        self.calls.append(path)
        if path == "/branches/main":
            self.main_calls += 1
            return {"commit": {"sha": B if self.drift and self.main_calls > 1 else A}}
        if path.startswith("/pulls?"):
            page = int(path.rsplit("=", 1)[-1])
            if page == 1:
                self.list_pass += 1
            if self.drift_pr_list and self.list_pass > 1:
                return []
            start = (page - 1) * 100 + 1
            batch = [{"number": n} for n in range(start, min(start + 100, self.count + 1))]
            if self.duplicate_page and page > 1:
                return [{"number": 1}]
            return batch
        if path.startswith("/pulls/"):
            number = int(path.split("/")[2])
            metadata, files = self.details[number]
            if "/files?per_page=100&page=" in path:
                page = int(path.rsplit("=", 1)[-1])
                portion = files[(page - 1) * 100: page * 100]
                return [] if self.truncate_files else copy.deepcopy(portion)
            self.detail_reads[number] = self.detail_reads.get(number, 0) + 1
            result = copy.deepcopy(metadata)
            if ((self.drift_head and self.detail_reads[number] > 1)
                    or (self.drift_head_at_completion and self.detail_reads[number] > 2)):
                result["head"]["sha"] = "c" * 40
            if self.boolean_metadata_on_read is not None:
                field, read_number = self.boolean_metadata_on_read
                if self.detail_reads[number] == read_number:
                    result[field] = True
            return result
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

    def test_paginates_101_changed_paths_and_keeps_all_evidence(self):
        api = FakeGitHub(1)
        info, _ = api.details[1]
        info["changed_files"] = 101
        all_files = [{"filename": f"docs/p{index}.md", "status": "modified"}
                     for index in range(101)]
        api.details[1] = (info, all_files)
        evidence = s.collect(api)
        self.assertEqual(101, len(evidence["pulls"][0]["files"]))
        self.assertEqual(101, assess(evidence)["files_scanned"])
        self.assertIn("/pulls/1/files?per_page=100&page=2", api.calls)

    def test_second_file_page_must_be_complete(self):
        api = FakeGitHub(1)
        info, _ = api.details[1]
        info["changed_files"] = 101
        api.details[1] = (info, [
            {"filename": f"docs/{i}.md", "status": "modified"}
            for i in range(100)])
        with self.assertRaisesRegex(InvalidInventory, "incomplete_pr_files_page"):
            s.collect(api)

    def test_duplicate_second_file_page_rejected(self):
        api = FakeGitHub(1)
        info, _ = api.details[1]
        info["changed_files"] = 101
        api.details[1] = (info, [
            {"filename": "docs/a.md", "status": "modified"}
            for _ in range(101)])
        with self.assertRaisesRegex(InvalidInventory, "unsafe_or_duplicate_file_path"):
            s.collect(api)

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
        api.details[1][0]["changed_files"] = 301
        with self.assertRaisesRegex(InvalidInventory, "incomplete_or_oversized_pr_files"):
            s.collect(api)

    def test_head_moving_between_detail_and_files_denied(self):
        api = FakeGitHub()
        api.drift_head = True
        with self.assertRaisesRegex(InvalidInventory, "pr_changed_during_snapshot"):
            s.collect(api)

    def test_pr_head_changes_after_other_pr_reads_are_denied(self):
        api = FakeGitHub(2)
        api.drift_head_at_completion = True
        with self.assertRaisesRegex(InvalidInventory,
                                    "pr_changed_before_snapshot_completed"):
            s.collect(api)

    def test_boolean_pr_identity_on_immediate_recheck_denied(self):
        for field in ("number", "changed_files"):
            api = FakeGitHub(count=1)
            api.boolean_metadata_on_read = (field, 2)
            with self.subTest(field=field):
                with self.assertRaisesRegex(InvalidInventory,
                                            "pr_changed_during_snapshot"):
                    s.collect(api)

    def test_boolean_pr_identity_on_final_recheck_denied(self):
        for field in ("number", "changed_files"):
            api = FakeGitHub(count=1)
            api.boolean_metadata_on_read = (field, 3)
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    InvalidInventory, "pr_changed_before_snapshot_completed"
                ):
                    s.collect(api)

    def test_open_pr_list_change_during_pagination_denied(self):
        api = FakeGitHub()
        api.drift_pr_list = True
        with self.assertRaisesRegex(InvalidInventory, "pr_inventory_changed_during_snapshot"):
            s.collect(api)

    def test_current_main_drift_denied(self):
        api = FakeGitHub()
        api.drift = True
        with self.assertRaisesRegex(InvalidInventory, "main_changed_during_snapshot"):
            s.collect(api)

    def test_invalid_main_shape_fails_closed(self):
        for candidate in (None, True, [], {"sha": True}, {"sha": "short"}):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(InvalidInventory, "untrusted_main_revision"):
                    s.collect(lambda _: {"commit": candidate})

    def test_boolean_pr_detail_number_is_not_accepted(self):
        api = FakeGitHub()
        api.details[1][0]["number"] = True
        with self.assertRaisesRegex(InvalidInventory, "wrong_pr_detail"):
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

    def test_github_reader_exact_origin_json_mime_and_get_only(self):
        class Response:
            def __init__(self, *, body=b'{"commit":{"sha":"a"}}',
                         url=s.API + "/branches/main", mime="application/json"):
                self.data = body
                self.url = url
                self.headers = Message()
                self.headers["Content-Type"] = mime
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self, limit):
                return self.data[:limit]

        class Opener:
            def __init__(self, response):
                self.response = response
                self.requests = []
            def open(self, request, timeout=None):
                self.requests.append(request)
                return self.response

        for response, error in (
            (Response(), None),
            (Response(url="https://malicious.invalid/repos/x"), "untrusted_github_response"),
            (Response(mime="text/html"), "untrusted_github_response"),
            (Response(body=b'{"a":1,"a":2}'), "duplicate_json_key"),
            (Response(body=b"x" * 2_000_001), "oversize_github_response"),
        ):
            with self.subTest(error=error):
                opener = Opener(response)
                with patch.object(s, "build_opener", return_value=opener):
                    get = s.github_reader("read-only-test")
                    if error is None:
                        self.assertEqual({"commit": {"sha": "a"}},
                                         get("/branches/main"))
                    else:
                        with self.assertRaisesRegex(InvalidInventory, error):
                            get("/branches/main")
                self.assertEqual(1, len(opener.requests))
                self.assertEqual("GET", opener.requests[0].get_method())
                self.assertEqual(s.API + "/branches/main",
                                 opener.requests[0].full_url)

    def test_reader_never_redirects_token_and_redacts_transport_errors(self):
        handler = s.DenyRedirect()
        with self.assertRaisesRegex(InvalidInventory, "github_redirect_denied"):
            handler.redirect_request(None, None, 302, "redirect", {},
                                     "https://malicious.invalid/steal")
        class FailingOpener:
            def open(self, request, timeout=None):
                raise URLError("sensitive-token-value")
        with patch.object(s, "build_opener", return_value=FailingOpener()):
            reader = s.github_reader("read-only-test")
            with self.assertRaisesRegex(InvalidInventory, "^github_read_failed$"):
                reader("/branches/main")

    def test_inventory_workflow_is_owner_main_only_and_hosted_readonly(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows" /
                    "zcloud-pr-owner-collision-inventory-20261015.yml").read_text(
                        encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "contents: read",
            "pull-requests: read",
            "runs-on: ubuntu-latest",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "persist-credentials: false",
            "ref: ${{ github.sha }}",
            "git -c credential.helper= ls-remote",
            "zcloud_deployops_pr_owner_snapshot_20261015",
            "zcloud_deployops_pr_owner_collision_20261015.py",
            "set +e",
            "exit \"$status\"",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        for forbidden in (
            "pull_request_target:", "workflow_run:", "push:", "schedule:",
            "contents: write", "actions: write", "pull-requests: write",
            "self-hosted", "systemctl", "sudo ", "git push",
            "deploy:", "zcloud-production-deploy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_hosted_workflow_rechecks_canonical_main_after_snapshot(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows" /
                "zcloud-pr-owner-collision-inventory-20261015.yml").read_text(
                    encoding="utf-8")
        self.assertGreaterEqual(text.count("ls-remote https://github.com/Zennay/zCloud.git"), 2)
        first = text.find("Prove exact current main")
        read = text.find("Fetch exact-main, fully paginated PR ownership")
        report = text.find("Print bounded advisory result")
        final = text.rfind("ls-remote https://github.com/Zennay/zCloud.git")
        self.assertTrue(0 <= first < read < report < final)
        self.assertIn("GIT_TERMINAL_PROMPT=0 timeout 20 git", text)
        self.assertIn('test "$current" = "$GITHUB_SHA"', text[final:])
        self.assertIn("Main snapshot SHA:", text)
        self.assertIn("Evidence fingerprint (unverified SHA-256):", text)
        self.assertIn('exit "$status"', text[final:])

    def test_no_release_or_runtime_mutation_codepaths(self):
        from pathlib import Path
        code = Path(s.__file__).read_text(encoding="utf-8")
        self.assertIn('method="GET"', code)
        for forbidden in ("method=\"POST\"", "method=\"PUT\"", "method=\"DELETE\"",
                          "unlink(", "systemctl ", "git push", "sqlite3.connect", "GITHUB_TOKEN="):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
