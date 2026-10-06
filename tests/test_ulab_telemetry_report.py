import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_ulab_telemetry_report as report


NOW = datetime(2026, 10, 6, 20, 20, tzinfo=timezone.utc)
SHA = "a" * 40


class FakeGitHub:
    def __init__(self, *, pulls=None, runs=None, status=None):
        self.pulls = [] if pulls is None else pulls
        self.runs = [] if runs is None else runs
        self.status = {"state": "pending", "statuses": []} if status is None else status
        self.urls = []

    def __call__(self, url, token):
        self.urls.append(url)
        if url.endswith("/commits/main"):
            return {"sha": SHA}
        if "/pulls?" in url:
            return self.pulls
        if "/actions/runs?" in url:
            return {"workflow_runs": self.runs}
        if url.endswith(f"/commits/{SHA}/status"):
            return self.status
        raise AssertionError(url)


class ULabTelemetryReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-ulab-telemetry-")
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects.json"
        self.projects.write_text(json.dumps([
            {
                "id": "ulab",
                "status": "active",
                "phase": "V2 proof gate",
                "next_step": "Run real maintainer trial",
                "milestone_revision": "score-v2",
                "progress_basis": "Weighted V0-V3 score",
                "progress_override": 38,
                "milestones": [
                    {"title": "V0 Core", "done": True, "progress": 100},
                    {"title": "V1 CLI", "done": False, "progress": 75},
                    {"title": "V2 OSS", "done": False, "progress": 13},
                ],
                "human_gates": [
                    {
                        "id": "ulab-maintainer",
                        "severity": "urgent",
                        "action": "Do one real maintainer usability test",
                    }
                ],
            },
            {"id": "other", "status": "active"},
        ]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, github):
        return report.build_report(self.projects, now=NOW, getter=github)

    def test_combines_scorecard_gate_main_and_open_pr_evidence(self):
        github = FakeGitHub(pulls=[{"number": 12}, {"number": 7}])
        payload = self.build(github)
        self.assertEqual("ulab", payload["project_id"])
        self.assertEqual(38.0, payload["scorecard"]["overall_progress"])
        self.assertEqual(3, len(payload["scorecard"]["milestones"]))
        self.assertEqual("ulab-maintainer", payload["implementation_gates"][0]["id"])
        self.assertEqual(SHA, payload["github"]["main_sha"])
        self.assertEqual([7, 12], payload["github"]["open_pr_numbers"])
        self.assertTrue(all(url.startswith("https://api.github.com/repos/Zennay/uLab") for url in github.urls))

    def test_missing_ci_is_explicitly_not_configured_never_green(self):
        payload = self.build(FakeGitHub(status={"state": "pending", "statuses": []}))
        self.assertEqual("not_configured", payload["github"]["ci"]["status"])
        self.assertEqual("github_exact_head_no_ci_evidence", payload["github"]["ci"]["source"])
        self.assertTrue(payload["evidence_contract"]["ci_missing_is_never_green"])

    def test_exact_head_actions_success_and_failure_are_classified(self):
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

    def test_malformed_scorecard_and_gate_evidence_fails_closed(self):
        raw = json.loads(self.projects.read_text(encoding="utf-8"))
        raw[0]["milestones"][0]["progress"] = True
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "milestone progress must be numeric"):
            self.build(FakeGitHub())

        raw[0]["milestones"][0]["progress"] = 100
        raw[0]["human_gates"].append(dict(raw[0]["human_gates"][0]))
        self.projects.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate human gate id"):
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
        bad = FakeGitHub(pulls=[{"number": True}])
        with self.assertRaisesRegex(RuntimeError, "pull request row"):
            self.build(bad)

        def invalid_commit(url, token):
            if url.endswith("/commits/main"):
                return {"sha": "short"}
            raise AssertionError(url)
        with self.assertRaisesRegex(RuntimeError, "main commit SHA"):
            report.build_report(self.projects, now=NOW, getter=invalid_commit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
