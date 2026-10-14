"""No-title/no-auth GitHub Actions summary for autonomous PR source integration."""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from scripts import zcloud_automerge_report_20261014 as report
from scripts import zcloud_autonomous_pr_integrator as engine

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-autonomous-safe-pr-integration.yml"


def fixture():
    return {
        "repo": "Zennay/zCloud", "mode": "apply",
        "inventory": {
            "open_prs_scanned": 147,
            "explicitly_labeled": 2, "draft_prs": 90,
            "unlabeled_non_draft": 55, "review_only_pr_ids": [12, 13],
        },
        "receipts": [{"pr": 7, "decision": "not_review_ready"},
                     {"pr": 9, "decision": "required_smoke_not_green"}],
    }


class SummaryTests(unittest.TestCase):
    def test_plain_dutch_operational_backlog_no_implicit_merge_right(self):
        item = fixture()
        item["inventory"]["explicitly_labeled"] = 0
        item["inventory"]["unlabeled_non_draft"] = 57
        item["receipts"] = []
        text = report.summary(item)
        self.assertIn("147", text)
        self.assertIn("verplichte expliciete label", text)
        self.assertIn("#12, #13", text)
        self.assertIn("NIET goedgekeurd voor merge", text)
        self.assertIn("productie-uitrol", text)
        self.assertNotIn("authorize automatically", text)

    def test_bounded_classifications_not_user_supplied_titles(self):
        item = fixture()
        md = report.summary(item)
        self.assertIn("required_smoke_not_green", md)
        self.assertIn("not_review_ready", md)
        self.assertNotIn("github.com/attacker", md)
        self.assertNotIn("Zennay/zCloud/pull/", md)
        self.assertIn("Door de integrator gerapporteerde bronmerges:** 0", md)

    def test_one_verified_merge_is_allowed_only_in_apply_mode(self):
        sample = fixture()
        sample["receipts"] = [{"pr": 7, "decision": "merged", "merge_sha": "a"*40}]
        self.assertIn("Door de integrator gerapporteerde bronmerges:** 1", report.summary(sample))
        for change in ({"mode": "dry_run"},
                       {"receipts": [{"pr": 7, "decision": "merged"}]},
                       {"receipts": [{"pr": 7, "decision": "merged", "merge_sha": "x"*40}]},
                       {"receipts": [sample["receipts"][0], sample["receipts"][0]]}):
            with self.subTest(change=change), self.assertRaises(report.InvalidReport):
                report.summary({**sample, **change})

    def test_invalid_counts_and_injections_fail_closed(self):
        for key, val in (("open_prs_scanned", True),
                         ("open_prs_scanned", -1), ("open_prs_scanned", 5000),
                         ("explicitly_labeled", 200), ("draft_prs", True),
                         ("unlabeled_non_draft", 200),
                         ("review_only_pr_ids", [1, True]),
                         ("review_only_pr_ids", [1] * 11)):
            sample = fixture()
            sample["inventory"][key] = val
            with self.subTest(key=key, val=str(val)), self.assertRaises(report.InvalidReport):
                report.summary(sample)
        for bad in ('<script>alert(1)</script>', 'merged|X', 'wrong-actor', 'A' * 60):
            sample = fixture()
            sample["receipts"][0]["decision"] = bad
            with self.subTest(reason=bad), self.assertRaises(report.InvalidReport):
                report.summary(sample)

    def test_disjoint_draft_and_review_only_cohorts_must_add_up(self):
        for draft, review, scanned in ((90, 60, 147), (148, 0, 147),
                                      (0, 148, 147), (100, 48, 147)):
            item = fixture()
            item["inventory"].update({
                "draft_prs": draft, "unlabeled_non_draft": review,
                "open_prs_scanned": scanned,
            })
            with self.subTest(draft=draft, review=review, scanned=scanned):
                with self.assertRaises(report.InvalidReport):
                    report.summary(item)

    def test_unlabeled_review_ids_cannot_exceed_cohort_or_overlap_labelled(self):
        item = fixture()
        item["inventory"]["unlabeled_non_draft"] = 1
        with self.assertRaisesRegex(report.InvalidReport, "inconsistent_review_ids"):
            report.summary(item)
        item = fixture()
        item["inventory"]["review_only_pr_ids"] = [7, 13]
        with self.assertRaisesRegex(report.InvalidReport, "contradictory_pr_cohorts"):
            report.summary(item)

    def test_valid_partial_review_sample_does_not_require_all_pr_numbers(self):
        item = fixture()
        item["inventory"]["review_only_pr_ids"] = []
        self.assertIn("Open PR's gecontroleerd:** 147", report.summary(item))
        item["inventory"]["review_only_pr_ids"] = [12, 13]
        self.assertIn("#12, #13", report.summary(item))

    def test_bounded_strict_json_and_missing_evidence_summary(self):
        for content in (b'{"repo":"A","repo":"B"}', b'{"x":NaN}',
                        b'[]', b'{' + b'a'*300000 + b'}'):
            with tempfile.TemporaryDirectory() as d:
                inp = pathlib.Path(d) / "input.json"
                inp.write_bytes(content)
                with self.subTest(size=len(content)), self.assertRaises(report.InvalidReport):
                    report.read_report(inp)
        with tempfile.TemporaryDirectory() as d:
            dest = pathlib.Path(d) / "step-summary.md"
            self.assertEqual(report.main([str(pathlib.Path(d) / "absent"), str(dest)]), 2)
            self.assertIn("bewijs ontbreekt of is ongeldig", dest.read_text())
            inp = pathlib.Path(d) / "valid.json"
            inp.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(report.main([str(inp), str(dest)]), 0)
            self.assertIn("Open PR\u0027s gecontroleerd", dest.read_text())

    def test_integration_engine_reports_zero_opted_in_without_auto_labeling(self):
        B = "b" * 40
        env = {
            "GITHUB_REPOSITORY": engine.REPO, "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": B, "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_TOKEN": "test-token", "ZCLOUD_AUTOMERGE_ENABLED": "true",
            "GITHUB_ACTOR": "Zennay", "GITHUB_TRIGGERING_ACTOR": "Zennay",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        def transport(token, method, path, data=None):
            self.assertEqual(method, "GET")
            if path == "/branches/main":
                return {"commit": {"sha": B}}
            if path.startswith("/pulls?"):
                return [
                    {"number": 101, "draft": True, "labels": []},
                    {"number": 102, "draft": False, "labels": []},
                    {"number": 103, "draft": False, "labels": []},
                ]
            self.fail("no approval means no file/check/merge inspection")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(engine.main(["--apply"], env, transport), 0)
        data = json.loads(buf.getvalue())
        inv = data["inventory"]
        self.assertEqual(inv["open_prs_scanned"], 3)
        self.assertEqual(inv["explicitly_labeled"], 0)
        self.assertEqual(inv["draft_prs"], 1)
        self.assertEqual(inv["unlabeled_non_draft"], 2)
        self.assertEqual(inv["review_only_pr_ids"], [102, 103])
        self.assertEqual(data["receipts"], [])
        self.assertIn("verplichte expliciete label", report.summary(data))

    def test_report_writer_always_closes_file_without_resource_warning(self):
        import warnings
        with tempfile.TemporaryDirectory() as d:
            input_path = pathlib.Path(d) / "report.json"
            output_path = pathlib.Path(d) / "summary.md"
            input_path.write_text(json.dumps(fixture()), encoding="utf-8")
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always", ResourceWarning)
                self.assertEqual(report.main([str(input_path), str(output_path)]), 0)
                self.assertEqual(report.main([str(input_path), str(output_path)]), 0)
            self.assertFalse([entry for entry in seen
                              if issubclass(entry.category, ResourceWarning)])
            self.assertEqual(output_path.read_text().count("## zCloud autonome PR-integratie — bronoverzicht"), 2)

    def test_existing_hourly_pipeline_receipts_are_not_prod_authority(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("tee \"$RUNNER_TEMP/zcloud-automerge-report.json\"", wf)
        self.assertIn("set -euo pipefail", wf)
        self.assertIn("scripts/zcloud_automerge_report_20261014.py", wf)
        self.assertIn("if: always()", wf)
        self.assertIn("GITHUB_STEP_SUMMARY", wf)
        self.assertIn("cron: '17 * * * *'", wf)
        self.assertNotIn("runs-on: [self-hosted", wf)
        self.assertNotIn("systemctl ", wf)
        self.assertNotIn("gh workflow run", wf)


if __name__ == "__main__":
    unittest.main()
