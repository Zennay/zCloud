import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts import zcloud_self_telemetry_report as report


NOW = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
SHA = "a" * 40


class FakeGitHub:
    def __init__(self, *, pulls=None, pull_pages=None, runs=None, status=None):
        self.pulls = [] if pulls is None else pulls
        self.pull_pages = pull_pages
        self.runs = [] if runs is None else runs
        self.status = {"state": "pending", "statuses": []} if status is None else status
        self.urls = []

    def __call__(self, url, token):
        self.urls.append(url)
        if url.endswith("/commits/main"):
            return {"sha": SHA}
        if "/pulls?" in url:
            if self.pull_pages is None:
                return self.pulls
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            return self.pull_pages.get(page, [])
        if "/actions/runs?" in url:
            return {"workflow_runs": self.runs}
        if url.endswith(f"/commits/{SHA}/status"):
            return self.status
        raise AssertionError(url)


class ZCloudSelfTelemetryReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-self-telemetry-")
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects.json"
        self.projects.write_text(json.dumps([
            {
                "id": "cloud",
                "status": "active",
                "phase": "Accelerate",
                "next_step": "Advance a free control-plane slice",
                "milestone_revision": "cloud-v1",
                "progress_basis": "Evidence-backed control-plane milestones",
                "milestones": [
                    {"title": "Safety", "done": True, "progress": 100},
                    {"title": "Observability", "done": False, "progress": 60},
                ],
            },
            {"id": "other", "status": "active"},
        ]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, github):
        return report.build_report(self.projects, now=NOW, getter=github)

    def test_combines_scorecard_and_exact_main_github_evidence(self):
        github = FakeGitHub(pulls=[{"number": 12}, {"number": 7}])
        payload = self.build(github)
        self.assertEqual("cloud", payload["project_id"])
        self.assertEqual(80.0, payload["scorecard"]["overall_progress"])
        self.assertEqual(2, len(payload["scorecard"]["milestones"]))
        self.assertEqual(SHA, payload["github"]["main_sha"])
        self.assertEqual(2, payload["github"]["open_pr_count"])
        self.assertEqual([12, 7], payload["github"]["open_pr_numbers"])
        self.assertFalse(payload["github"]["open_pr_numbers_truncated"])
        self.assertTrue(all(url.startswith("https://api.github.com/repos/Zennay/zCloud") for url in github.urls))
        self.assertTrue(payload["evidence_contract"]["runtime_state_is_not_mutated"])

    def test_open_pr_inventory_paginates_but_emits_bounded_numbers(self):
        first = [{"number": n} for n in range(300, 200, -1)]
        second = [{"number": 200}, {"number": 199}]
        payload = self.build(FakeGitHub(pull_pages={1: first, 2: second}))
        github = payload["github"]
        self.assertEqual(102, github["open_pr_count"])
        self.assertEqual(report.MAX_PR_NUMBERS_EMITTED, len(github["open_pr_numbers"]))
        self.assertTrue(github["open_pr_numbers_truncated"])
        self.assertEqual(300, github["open_pr_numbers"][0])

    def test_missing_ci_is_explicitly_not_configured_never_green(self):
        payload = self.build(FakeGitHub(status={"state": "pending", "statuses": []}))
        self.assertEqual("not_configured", payload["github"]["ci"]["status"])
        self.assertEqual("github_exact_head_no_ci_evidence", payload["github"]["ci"]["source"])
        self.assertTrue(payload["evidence_contract"]["ci_missing_is_never_green"])

    def test_exact_head_actions_success_failure_and_running_are_classified(self):
        success = FakeGitHub(runs=[
            {"head_sha": SHA, "status": "completed", "conclusion": "success"},
            {"head_sha": "b" * 40, "status": "completed", "conclusion": "failure"},
        ])
        self.assertEqual("success", self.build(success)["github"]["ci"]["status"])

        failure = FakeGitHub(runs=[
            {"head_sha": SHA, "status": "completed", "conclusion": "failure"},
        ])
        self.assertEqual("failure", self.build(failure)["github"]["ci"]["status"])

        running = FakeGitHub(runs=[
            {"head_sha": SHA, "status": "in_progress", "conclusion": None},
        ])
        self.assertEqual("in_progress", self.build(running)["github"]["ci"]["status"])

    def test_commit_status_context_is_used_when_actions_are_absent(self):
        github = FakeGitHub(status={
            "state": "success",
            "statuses": [{"context": "external-ci", "state": "success"}],
        })
        ci = self.build(github)["github"]["ci"]
        self.assertEqual("success", ci["status"])
        self.assertEqual("github_commit_status_exact_head", ci["source"])
        self.assertEqual(1, ci["status_context_count"])

    def test_commit_status_wins_over_historical_cancelled_actions(self):
        github = FakeGitHub(
            runs=[{"head_sha": SHA, "status": "completed", "conclusion": "cancelled"}],
            status={
                "state": "success",
                "statuses": [{"context": "zcloud/vps-production", "state": "success"}],
            },
        )
        ci = self.build(github)["github"]["ci"]
        self.assertEqual("success", ci["status"])
        self.assertEqual("github_commit_status_exact_head", ci["source"])
        self.assertEqual(1, ci["run_count"])

    def test_malformed_scorecard_fails_closed(self):
        raw = json.loads(self.projects.read_text(encoding="utf-8"))
        raw[0]["milestones"][0]["progress"] = True
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "milestone progress must be numeric"):
            self.build(FakeGitHub())

        raw[0]["milestones"][0]["progress"] = 100
        raw[0]["milestones"][0]["done"] = "true"
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "milestone done must be boolean"):
            self.build(FakeGitHub())

    def test_duplicate_or_archived_cloud_project_fails_closed(self):
        raw = json.loads(self.projects.read_text(encoding="utf-8"))
        raw.append(dict(raw[0]))
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one cloud"):
            self.build(FakeGitHub())

        self.projects.write_text(json.dumps([dict(raw[0], status="archived")]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "archived"):
            self.build(FakeGitHub())

    def test_completed_milestone_requires_full_progress(self):
        raw = json.loads(self.projects.read_text(encoding="utf-8"))
        raw[0]["milestones"][0]["progress"] = 99
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "completed milestone"):
            self.build(FakeGitHub())

    def test_symlink_projects_file_is_rejected(self):
        link = self.root / "projects-link.json"
        try:
            link.symlink_to(self.projects)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            report.build_report(link, now=NOW, getter=FakeGitHub())

    def test_invalid_github_shapes_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "pull request row"):
            self.build(FakeGitHub(pulls=[{"number": True}]))

        duplicate_page = [{"number": n} for n in range(100, 0, -1)]
        with self.assertRaisesRegex(RuntimeError, "duplicate numbers"):
            self.build(FakeGitHub(pull_pages={1: duplicate_page, 2: [{"number": 100}]}))

        def invalid_commit(url, token):
            if url.endswith("/commits/main"):
                return {"sha": "short"}
            raise AssertionError(url)

        with self.assertRaisesRegex(RuntimeError, "main commit SHA"):
            report.build_report(self.projects, now=NOW, getter=invalid_commit)

    def test_report_does_not_emit_pr_titles_or_remote_payloads(self):
        payload = self.build(FakeGitHub(pulls=[
            {"number": 1, "title": "sensitive free text", "body": "do not project me"}
        ]))
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("sensitive free text", encoded)
        self.assertNotIn("do not project me", encoded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
