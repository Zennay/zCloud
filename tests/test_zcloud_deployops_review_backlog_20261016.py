"""Offline adversarial regressions for the read-only deploy-ops PR brief."""
import io
import json
from contextlib import redirect_stderr
from datetime import datetime, timezone
from unittest import TestCase, mock

from scripts import zcloud_deployops_review_backlog_20261016 as brief

NOW = datetime(2026, 10, 16, 11, tzinfo=timezone.utc)
SHA = "a" * 40
OTHER = "b" * 40


def pr(n, **changes):
    item = {"number": n, "title": f"Review feature {n}", "draft": True,
            "html_url": f"https://github.com/Zennay/zCloud/pull/{n}",
            "head": {"sha": SHA}, "base": {"ref": "main", "sha": SHA},
            "updated_at": "2026-10-01T08:00:00Z"}
    item.update(changes)
    return item


class Reader:
    def __init__(self, pages, mains=None):
        self.pages = pages
        self.mains = iter(mains or [SHA, SHA])
        self.calls = []

    def __call__(self, kind, page):
        self.calls.append((kind, page))
        if kind == "main":
            return {"commit": {"sha": next(self.mains)}}
        return self.pages.get(page, [])


class BacklogBriefTests(TestCase):
    def test_full_two_pass_pagination_and_operator_brief(self):
        rows = [pr(n) for n in range(1, 102)]
        reader = Reader({1: rows[:100], 2: rows[100:]})
        found = brief.collect(reader, NOW)
        self.assertEqual(101, len(found["prs"]))
        self.assertEqual([("main", 0), ("pulls", 1), ("pulls", 2),
                          ("pulls", 1), ("pulls", 2), ("main", 0)], reader.calls)
        output = brief.render(found)
        self.assertIn("101 open PR", output)
        self.assertIn("[#1]", output)
        self.assertIn("Geen merge-, release-", output)
        self.assertNotIn("Production deploy green", output)
        self.assertFalse(found["merge_authorized"])
        self.assertFalse(found["deploy_authorized"])

    def test_no_prs_is_complete_if_both_passes_empty(self):
        reader = Reader({1: []})
        report = brief.collect(reader, NOW)
        self.assertIn("0 open PR", brief.render(report))

    def test_main_drift_denied(self):
        with self.assertRaisesRegex(brief.Denied, "main_drift"):
            brief.collect(Reader({1: [pr(1)]}, mains=[SHA, OTHER]), NOW)

    def test_full_inventory_changing_between_passes_denied(self):
        calls = 0
        def reader(kind, page):
            nonlocal calls
            if kind == "main":
                return {"commit": {"sha": SHA}}
            calls += 1
            return [pr(1, title="Original" if calls == 1 else "Changed")]
        with self.assertRaisesRegex(brief.Denied, "pr_drift"):
            brief.collect(reader, NOW)

    def test_duplicate_pr_number_across_pages_denied(self):
        reader = Reader({1: [pr(n) for n in range(1, 101)],
                         2: [pr(30)]})
        with self.assertRaisesRegex(brief.Denied, "duplicate_or_bad_number"):
            brief.collect(reader, NOW)

    def test_page_over_cap_denied_instead_of_partial_success(self):
        def reader(kind, page):
            if kind == "main":
                return {"commit": {"sha": SHA}}
            return [pr(page * 100 + n) for n in range(100)]
        with self.assertRaisesRegex(brief.Denied, "unbounded_or_incomplete"):
            brief.collect(reader, NOW)

    def test_invalid_and_forged_pr_metadata_denied(self):
        bad = [
            {"number": True}, {"number": 0}, {"title": ""}, {"title": "x" * 257},
            {"draft": "false"}, {"html_url": "https://evil.test/pull/1"},
            {"head": {"sha": "short"}}, {"base": {"ref": 1, "sha": SHA}},
            {"updated_at": "2026-99-99T00:00:00Z"},
            {"updated_at": "2027-01-01T00:00:00Z"},
        ]
        for changes in bad:
            with self.subTest(changes=changes):
                with self.assertRaises(brief.Denied):
                    brief.collect(Reader({1: [pr(1, **changes)]}), NOW)

    def test_deceptive_titles_do_not_break_operator_markdown(self):
        report = brief.collect(Reader({1: [pr(4, title="oops | [link]<script>\nSAFE")]}), NOW)
        content = brief.render(report)
        self.assertIn("oops \\| \\[link\\]\\<script\\>", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("\nSAFE |", content)

    def test_missing_authorization_disallowed_even_with_valid_prs(self):
        report = brief.collect(Reader({1: [pr(1)]}), NOW)
        report["merge_authorized"] = True
        with self.assertRaisesRegex(brief.Denied, "untrusted_inventory"):
            brief.render(report)

    def test_duplicate_json_keys_and_nan_refused(self):
        with self.assertRaisesRegex(brief.Denied, "duplicate_json_key"):
            json.loads('{"a":1,"a":2}', object_pairs_hook=brief._unique)
        with self.assertRaisesRegex(brief.Denied, "invalid_number"):
            json.loads('{"a":NaN}', parse_constant=brief._bad_number)

    def test_cli_on_incomplete_inventory_fails_without_report(self):
        with mock.patch.object(brief, "collect", side_effect=brief.Denied("details")):
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(2, brief.main([]))
                self.assertNotIn("details", stderr.getvalue())
                self.assertIn("DENIED", stderr.getvalue())

    def test_workflow_checkout_sha_mismatch_denied(self):
        report = brief.collect(Reader({1: [pr(1)]}), NOW)
        with mock.patch.object(brief, "collect", return_value=report):
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(2, brief.main(["--expected-main-sha", OTHER]))
                self.assertIn("DENIED", stderr.getvalue())

    def test_public_http_fetch_accepts_exact_json(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json; charset=utf-8"}
            def __init__(self, url, raw):
                self.url, self.raw = url, raw
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self, size):
                return self.raw[:size]
        url = brief.API + "/branches/main"
        with mock.patch.object(brief.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(
                url, json.dumps({"commit": {"sha": SHA}}).encode())
            self.assertEqual(SHA, brief.fetch("main")["commit"]["sha"])

    def test_public_http_fetch_denies_redirect_wrong_mime_and_oversize(self):
        class Response:
            status = 200
            def __init__(self, url, raw, mime):
                self.url, self.raw = url, raw
                self.headers = {"Content-Type": mime}
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self, size):
                return self.raw[:size]
        url = brief.API + "/branches/main"
        samples = [
            Response("https://evil.example/branches/main", b"{}", "application/json"),
            Response(url, b"{}", "text/html"),
            Response(url, b"x" * (brief.MAX_BYTES + 1), "application/json"),
        ]
        for fake in samples:
            with self.subTest(url=fake.url, length=len(fake.raw)):
                with mock.patch.object(brief.urllib.request, "build_opener") as opener:
                    opener.return_value.open.return_value = fake
                    with self.assertRaises(brief.Denied):
                        brief.fetch("main")

    def test_public_http_duplicate_json_denied_without_output(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return brief.API + "/branches/main"
            def read(self, size):
                return b'{"commit":{},"commit":{}}'
        with mock.patch.object(brief.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaises(brief.Denied):
                brief.fetch("main")

    def test_reader_never_accepts_other_method_or_url(self):
        with self.assertRaisesRegex(brief.Denied, "unsupported_endpoint"):
            brief.fetch("workflow_dispatch", 1)
        with self.assertRaisesRegex(brief.Denied, "unsupported_endpoint"):
            brief.fetch("pulls", 0)

    def test_out_of_order_page_entries_still_deterministic(self):
        rows = [pr(7), pr(4)]
        a = brief.collect(Reader({1: rows}), NOW)
        b = brief.collect(Reader({1: list(reversed(rows))}), NOW)
        self.assertEqual(a["prs"], b["prs"])
        self.assertEqual(brief.render(a), brief.render(b))
