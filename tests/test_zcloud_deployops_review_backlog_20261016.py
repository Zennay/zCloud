"""Offline adversarial regressions for the read-only deploy-ops PR brief."""
import io
import json
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
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


def issue(n, *, is_pr=False, labels=None, **changes):
    row = {
        "number": n, "title": f"Issue {n}",
        "html_url": f"https://github.com/Zennay/zCloud/{'pull' if is_pr else 'issues'}/{n}",
        "updated_at": "2026-10-01T08:00:00Z",
        "labels": ([{"name": name} for name in labels] if isinstance(labels, (list, tuple))
                   else [] if labels is None else labels),
        "assignees": [],
    }
    if is_pr:
        row["pull_request"] = {"url": f"https://api.github.com/repos/Zennay/zCloud/pulls/{n}"}
    row.update(changes)
    return row


class CombinedReader(Reader):
    def __init__(self, prs, issues, mains=None):
        super().__init__(prs, mains=mains)
        self.issues = issues

    def __call__(self, kind, page):
        if kind == "issues":
            self.calls.append((kind, page))
            return self.issues.get(page, [])
        return super().__call__(kind, page)


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
        self.assertIn("Uitleesstart (UTC): 2026-10-16T11:00:00Z", output)
        self.assertIn("geen atomair momentoverzicht", output)
        self.assertIn("[#1]", output)
        self.assertIn("Geen merge-, release-", output)
        self.assertNotIn("Production deploy green", output)
        self.assertFalse(found["merge_authorized"])
        self.assertFalse(found["deploy_authorized"])

    def test_review_attention_shortlist_only_non_draft_main_prs(self):
        rows = [
            pr(1, draft=False, title="ready to examine", updated_at="2026-10-01T08:00:00Z"),
            pr(2, draft=True, title="draft must not show"),
            pr(3, draft=False, base={"ref": "release", "sha": SHA},
               title="non-main must not show"),
            pr(4, draft=False, title="newer review candidate",
               updated_at="2026-10-10T08:00:00Z"),
        ]
        output = brief.render(brief.collect(Reader({1: rows}), NOW))
        self.assertIn("2 niet-concept-PR's gericht op main", output)
        shortlist = output.split("### PR's voor onafhankelijke beoordeling", 1)[1]
        self.assertLess(shortlist.index("[#4]"), shortlist.index("[#1]"))
        self.assertNotIn("[#2]", shortlist)
        self.assertNotIn("[#3]", shortlist)
        self.assertIn("niet mergegoedgekeurd", output)
        self.assertIn("Niet automatisch mergegereed", output)

    def test_review_attention_shortlist_limits_rows_and_never_claims_ci(self):
        report = brief.collect(
            Reader({1: [pr(n, draft=False) for n in range(1, 21)]}), NOW)
        output = brief.render(report)
        shortlist = output.split("### PR's voor onafhankelijke beoordeling", 1)[1]
        self.assertEqual(12, shortlist.count("https://github.com/Zennay/zCloud/pull/"))
        self.assertIn("20 niet-concept-PR's gericht op main", shortlist)
        self.assertIn("[#20]", shortlist)
        self.assertNotIn("[#1]", shortlist)
        self.assertIn("zonder CI-/eigenaarsoordeel", shortlist)

    def test_missing_or_naive_snapshot_clock_cannot_pass_as_fresh(self):
        report = brief.collect(Reader({1: [pr(1)]}), NOW)
        for clock in (None, "2026-10-16T11:00:00Z", NOW.replace(tzinfo=None)):
            with self.subTest(clock=clock):
                report["checked_at"] = clock
                with self.assertRaisesRegex(brief.Denied, "bad_snapshot_clock"):
                    brief.render(report)

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

    def test_untrusted_pr_and_issue_titles_cannot_format_fake_status(self):
        spoof = "**accepted** ![approval](https://evil.example/ok) | ~strike~"
        reader = CombinedReader(
            {1: [pr(7, title=spoof)]},
            {1: [issue(7, is_pr=True), issue(1, title=spoof)]})
        output = brief.render(brief.collect(reader, NOW, include_issues=True))
        self.assertIn(r"\*\*accepted\*\*", output)
        self.assertIn(r"\!\[approval\]\(https://evil\.example/ok\)", output)
        self.assertIn(r"\~strike\~", output)
        self.assertNotIn("![approval](", output)
        self.assertNotIn("**accepted**", output)
        self.assertEqual("\\" + chr(96), brief._safe(chr(96)))

    def test_safe_title_never_truncates_into_dangling_escape(self):
        value = "a" * 159 + "|" + "garbage" * 40
        escaped = brief._safe(value)
        self.assertTrue(escaped.endswith(r"\|"))
        self.assertNotIn("garbage", escaped)
        self.assertEqual(160, len(escaped.replace(r"\|", "|")))

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

    def test_issue_first_digest_counts_only_issues_not_pr_wrappers(self):
        reader = CombinedReader({1: [pr(10), pr(20)]}, {1: [
            issue(20, is_pr=True), issue(2, labels=["P1"], title="P1: fix"),
            issue(10, is_pr=True), issue(1, title="P0: runner failure"),
            issue(3, title="low-priority tidy"),
        ]})
        report = brief.collect(reader, NOW, include_issues=True)
        self.assertEqual(3, len(report["issues"]))
        self.assertEqual(2, len(report["prs"]))
        self.assertEqual([("main", 0), ("pulls", 1), ("pulls", 1),
                          ("issues", 1), ("issues", 1), ("pulls", 1),
                          ("main", 0)], reader.calls)
        summary = brief.render(report)
        self.assertIn("3 open issues", summary)
        self.assertIn("P0: 1, P1: 1", summary)
        self.assertLess(summary.index("[#1]"), summary.index("[#2]"))
        self.assertLess(summary.index("[#2]"), summary.index("[#3]"))
        self.assertIn("2 open PR", summary)
        self.assertFalse(report["merge_authorized"])

    def test_cross_stream_pr_head_drift_denied_after_issues_are_stable(self):
        """A PR can change while two individually stable /issues passes run."""
        pr_reads = 0
        def reader(kind, page):
            nonlocal pr_reads
            if kind == "main":
                return {"commit": {"sha": SHA}}
            if kind == "issues":
                return [issue(20, is_pr=True), issue(2)]
            pr_reads += 1
            head = SHA if pr_reads < 3 else OTHER
            return [pr(20, head={"sha": head})]
        with self.assertRaisesRegex(brief.Denied, "pr_drift_across_issue_scan"):
            brief.collect(reader, NOW, include_issues=True)
        self.assertEqual(3, pr_reads)

    def test_cross_stream_pr_title_and_draft_drift_denied(self):
        for changed in (
            {"title": "changed while issue pages were read"},
            {"draft": False},
            {"updated_at": "2026-10-02T08:00:00Z"},
            {"base": {"ref": "main", "sha": OTHER}},
        ):
            with self.subTest(changed=changed):
                pr_reads = 0
                def reader(kind, page):
                    nonlocal pr_reads
                    if kind == "main":
                        return {"commit": {"sha": SHA}}
                    if kind == "issues":
                        return [issue(20, is_pr=True)]
                    pr_reads += 1
                    return [pr(20, **(changed if pr_reads == 3 else {}))]
                with self.assertRaisesRegex(brief.Denied, "pr_drift_across_issue_scan"):
                    brief.collect(reader, NOW, include_issues=True)
                self.assertEqual(3, pr_reads)

    def test_pr_only_does_not_consume_extra_api_inventory(self):
        reader = Reader({1: [pr(20)]})
        brief.collect(reader, NOW, include_issues=False)
        self.assertEqual([("main", 0), ("pulls", 1), ("pulls", 1),
                          ("main", 0)], reader.calls)

    def test_issue_assignees_are_visible_but_never_ownership_admission(self):
        rows = [issue(1, title="P0: safe-triage", assignees=[{"login": "runner-owner"}]),
                issue(2)]
        report = brief.collect(CombinedReader({1: []}, {1: rows}),
                               NOW, include_issues=True)
        shown = brief.render(report)
        self.assertIn("runner-owner", shown)
        self.assertIn("niet toegewezen", shown)
        self.assertIn("niet vrij", shown)
        self.assertFalse(report["merge_authorized"])
        self.assertFalse(report["deploy_authorized"])

    def test_issue_assignee_assignment_change_invalidates_snapshot(self):
        calls = 0
        def reader(kind, page):
            nonlocal calls
            if kind == "main":
                return {"commit": {"sha": SHA}}
            if kind == "pulls":
                return []
            calls += 1
            assignees = [{"login": "worker1"}] if calls == 1 else [{"login": "worker2"}]
            return [issue(1, assignees=assignees)]
        with self.assertRaisesRegex(brief.Denied, "issue_drift"):
            brief.collect(reader, NOW, include_issues=True)

    def test_issue_pull_overlap_requires_exact_pr_cohort(self):
        for items in ([issue(10, is_pr=True), issue(99, is_pr=True)],
                      [issue(12, is_pr=True)]):
            with self.subTest(items=items):
                with self.assertRaisesRegex(brief.Denied, "pr_issue_cohort_mismatch"):
                    brief.collect(CombinedReader({1: [pr(10)]},
                                                  {1: items}), NOW, include_issues=True)

    def test_issue_inventory_drift_denied(self):
        calls = 0
        def reader(kind, page):
            nonlocal calls
            if kind == "main":
                return {"commit": {"sha": SHA}}
            if kind == "pulls":
                return []
            calls += 1
            return [issue(1, title="P0: old" if calls == 1 else "P0: changed")]
        with self.assertRaisesRegex(brief.Denied, "issue_drift"):
            brief.collect(reader, NOW, include_issues=True)

    def test_duplicate_issue_across_pages_denied(self):
        full_page = [issue(n) for n in range(1, 101)]
        reader = CombinedReader({1: []}, {1: full_page, 2: [issue(1)]})
        with self.assertRaisesRegex(brief.Denied, "bad_or_duplicate_issue"):
            brief.collect(reader, NOW, include_issues=True)

    def test_invalid_issue_metadata_is_denied(self):
        invalid = [
            {"number": True}, {"number": 0}, {"title": ""},
            {"labels": [{"name": ""}]}, {"labels": [{}]},
            {"labels": [{"name": "P0"}, {"name": "P0"}]},
            {"labels": "P0"}, {"pull_request": True},
            {"assignees": None}, {"assignees": [None]},
            {"assignees": [{"login": ""}]}, 
            {"assignees": [{"login": "alice"}, {"login": "alice"}]},
            {"html_url": "https://evil.test/issues/1"},
            {"updated_at": "2027-01-01T00:00:00Z"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(brief.Denied):
                    brief.collect(CombinedReader({1: []}, {1: [issue(1, **changes)]}),
                                  NOW, include_issues=True)

    def test_issue_page_hard_cap_denies_partial_result(self):
        def reader(kind, page):
            if kind == "main":
                return {"commit": {"sha": SHA}}
            if kind == "pulls":
                return []
            return [issue(page * 100 + i) for i in range(100)]
        with self.assertRaisesRegex(brief.Denied, "issue_pagination_cap"):
            brief.collect(reader, NOW, include_issues=True)

    def test_issue_priority_is_source_hint_not_guaranteed_severity(self):
        self.assertEqual(0, brief._priority("P0: crash", []))
        self.assertEqual(0, brief._priority("minor", ["P0"]))
        self.assertEqual(1, brief._priority("P1: issue", []))
        self.assertEqual(2, brief._priority("important P0 discussion", []))
        self.assertEqual(2, brief._priority("not marked", ["feature"]))

    def test_issue_render_does_not_interpret_untrusted_authority(self):
        reader = CombinedReader({1: []}, {1: [issue(1)]})
        report = brief.collect(reader, NOW, include_issues=True)
        report["deploy_authorized"] = True
        with self.assertRaisesRegex(brief.Denied, "untrusted_inventory"):
            brief.render(report)

    def test_cli_include_issues_is_explicit_and_non_authorizing(self):
        reader = CombinedReader({1: [pr(3)]}, {1: [issue(3, is_pr=True), issue(1)]})
        snapshot = brief.collect(reader, NOW, include_issues=True)
        with mock.patch.object(brief, "collect", return_value=snapshot) as collector:
            with mock.patch("builtins.print"):
                self.assertEqual(0, brief.main(["--include-issues", "--expected-main-sha", SHA]))
        collector.assert_called_once_with(include_issues=True)

    def test_hosted_summary_workflow_is_manual_main_only_and_read_only(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/zcloud-deployops-public-pr-backlog-brief.yml"
                    ).read_text(encoding="utf-8")
        self.assertIn("on:\n  workflow_dispatch:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("runs-on: self-hosted", workflow)
        for event in ("pull_request:", "pull_request_target:", "workflow_run:",
                      "\n  push:", "contents: write", "secrets: inherit"):
            with self.subTest(event=event):
                self.assertNotIn(event, workflow)

    def test_hosted_summary_workflow_requires_identity_and_bounded_cli(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/zcloud-deployops-public-pr-backlog-brief.yml"
                    ).read_text(encoding="utf-8")
        for boundary in ('test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
                         'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
                         'test "$GITHUB_REF" = "refs/heads/main"',
                         'test "$GITHUB_ACTOR" = "Zennay"',
                         'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
                         'test "$GITHUB_RUN_ATTEMPT" = "1"',
                         'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
                         '--expected-main-sha "$GITHUB_SHA"',
                         "--include-issues",
                         '--output "$GITHUB_STEP_SUMMARY"'):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, workflow)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_deployops_review_backlog_20261016",
                      workflow)

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
