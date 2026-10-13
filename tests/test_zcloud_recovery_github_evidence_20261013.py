"""Realistic GitHub REST payload adapter regression; all fixtures are offline."""
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_recovery_github_evidence_20261013 as evidence

S = "a" * 40
T = "b" * 40
REPO = {"id": 1389227570, "full_name": "Zennay/zCloud"}


def raw_run(number, workflow, *, head=S, status="completed", conclusion="success",
            event="workflow_run", attempt=1):
    return {
        "id": number, "run_attempt": attempt,
        "path": ".github/workflows/" + evidence.WORKFLOWS[workflow],
        "repository": dict(REPO), "head_repository": dict(REPO),
        "head_sha": head, "head_branch": "main",
        "status": status, "conclusion": conclusion, "event": event,
        "name": "ignored", "html_url": "https://github.com/secret"}


class FixtureGitHub:
    def __init__(self):
        self.calls = []
        self.main = S
        self.later_main = S
        self.statuses = [{
            "id": 44, "context": "zcloud/vps-production", "state": "failure"}]
        self.deploy = [raw_run(13, "deploy", conclusion="failure")]
        self.regression = [raw_run(17, "regression", event="push")]
        self.fail_endpoint = None
        self.responses = {}
        self.read_main_count = 0

    def __call__(self, path):
        self.calls.append(path)
        if path == self.fail_endpoint:
            raise evidence.SourceDenied("github_transport_unavailable")
        if path in self.responses:
            return self.responses[path]
        if path == "/commits/main":
            self.read_main_count += 1
            return {"sha": self.main if self.read_main_count == 1 else self.later_main}
        if path.startswith("/commits/") and path.endswith("/status"):
            return {"sha": self.main, "total_count": len(self.statuses),
                    "statuses": self.statuses}
        match = re.fullmatch(
            r"/actions/workflows/([^/]+)/runs\?head_sha=([a-f0-9]{40})"
            r"&branch=main&per_page=100&page=(\d+)", path)
        if not match:
            raise AssertionError("unexpected URL: " + path)
        workflow, sha, page = match.groups()
        if workflow == evidence.WORKFLOWS["deploy"]:
            rows = self.deploy
        elif workflow == evidence.WORKFLOWS["regression"]:
            rows = self.regression
        else:
            raise AssertionError("unexpected workflow: " + workflow)
        start = (int(page) - 1) * 100
        return {"total_count": len(rows), "workflow_runs": rows[start:start + 100]}


class GithubEvidenceTests(unittest.TestCase):
    def test_actual_main_failed_production_state_is_noop(self):
        fake = FixtureGitHub()
        result = evidence.observe(fake)
        self.assertEqual((result["decision"], result["reason"]),
                         ("NOOP", "production_status_present"))
        self.assertIs(result["authorized"], False)
        self.assertIs(result["mutations_performed"], False)
        self.assertEqual(fake.read_main_count, 2)
        self.assertEqual(len(fake.calls), 3, "failed production must not fetch workflows")
        self.assertNotIn("html_url", json.dumps(result))

    def test_empty_status_and_prior_terminal_deploy_denies(self):
        fake = FixtureGitHub()
        fake.statuses = []
        self.assertEqual(evidence.observe(fake)["reason"], "terminal_deploy_missing_status")

    def test_fully_idle_no_status_can_only_suggest_reservation(self):
        fake = FixtureGitHub()
        fake.deploy = []
        fake.statuses = []
        result = evidence.observe(fake)
        self.assertEqual(result["decision"], "READY_FOR_EXCLUSIVE_RESERVATION")
        self.assertIs(result["authorized"], False)

    def test_live_main_moved_during_snapshot_denied(self):
        fake = FixtureGitHub()
        fake.later_main = T
        self.assertEqual(evidence.observe(fake)["reason"], "main_changed_during_snapshot")

    def test_rejected_unknown_repo_or_workflow_path(self):
        for field, value in (("repository", {"id": 2, "full_name": "Zennay/zCloud"}),
                             ("head_repository", {"id": 2, "full_name": "Zennay/zCloud"}),
                             ("path", ".github/workflows/untrusted.yml")):
            fake = FixtureGitHub()
            fake.statuses = []
            fake.deploy[0][field] = value
            self.assertEqual(evidence.observe(fake)["decision"], "DENY")

    def test_reject_run_sha_or_branch_mismatch(self):
        for field, value in (("head_sha", T), ("head_branch", "feature")):
            fake = FixtureGitHub()
            fake.statuses = []
            fake.deploy[0][field] = value
            self.assertEqual(evidence.observe(fake)["decision"], "DENY")

    def test_reject_status_wrong_sha_and_count(self):
        fake = FixtureGitHub()
        fake.responses[f"/commits/{S}/status"] = {"sha": T, "total_count": 0, "statuses": []}
        self.assertEqual(evidence.observe(fake)["reason"], "production_status_wrong_sha")
        fake = FixtureGitHub()
        fake.responses[f"/commits/{S}/status"] = {"sha": S, "total_count": 3, "statuses": []}
        self.assertEqual(evidence.observe(fake)["reason"], "production_status_incomplete")

    def test_reject_unbounded_status_history(self):
        fake = FixtureGitHub()
        fake.statuses = [dict(id=i + 1, context="other", state="success") for i in range(101)]
        self.assertEqual(evidence.observe(fake)["reason"], "production_status_incomplete")

    def test_multipage_workflow_query_proves_all_runs(self):
        fake = FixtureGitHub()
        fake.statuses = []
        fake.regression = [raw_run(i + 1, "regression", event="push") for i in range(101)]
        snapshot = evidence.collect(fake)
        self.assertEqual(len(snapshot["regression"]["runs"]), 101)
        self.assertTrue(any("page=2" in path for path in fake.calls))
        self.assertEqual(evidence.classify(snapshot)["reason"], "terminal_deploy_missing_status")

    def test_workflow_second_page_missing_denies(self):
        fake = FixtureGitHub()
        fake.statuses = []
        fake.regression = [raw_run(i + 1, "regression", event="push") for i in range(101)]
        page = f"/actions/workflows/{evidence.WORKFLOWS['regression']}/runs?head_sha={S}&branch=main&per_page=100&page=2"
        fake.responses[page] = {"total_count": 101, "workflow_runs": []}
        self.assertEqual(evidence.observe(fake)["reason"], "workflow_page_truncated")

    def test_workflow_total_count_drift_denies(self):
        fake = FixtureGitHub()
        fake.statuses = []
        fake.regression = [raw_run(i + 1, "regression", event="push") for i in range(101)]
        page = f"/actions/workflows/{evidence.WORKFLOWS['regression']}/runs?head_sha={S}&branch=main&per_page=100&page=2"
        fake.responses[page] = {"total_count": 102, "workflow_runs": fake.regression[100:]}
        self.assertEqual(evidence.observe(fake)["reason"], "workflow_total_changed")

    def test_deny_duplicate_run_id_across_pages(self):
        fake = FixtureGitHub()
        fake.statuses = []
        fake.regression = [raw_run(i + 1, "regression", event="push") for i in range(101)]
        fake.regression[100]["id"] = fake.regression[0]["id"]
        self.assertEqual(evidence.observe(fake)["reason"], "workflow_duplicate_run_id")

    def test_max_page_limit_is_fail_closed(self):
        fake = FixtureGitHub()
        fake.statuses = []
        fake.regression = [raw_run(i + 1, "regression", event="push") for i in range(1001)]
        self.assertEqual(evidence.observe(fake)["reason"], "unbounded_or_missing_workflow_page")

    def test_missing_api_response_denies(self):
        fake = FixtureGitHub()
        fake.fail_endpoint = f"/commits/{S}/status"
        self.assertEqual(evidence.observe(fake)["reason"], "github_transport_unavailable")

    def test_accepted_evidence_is_sanitized(self):
        fake = FixtureGitHub()
        fake.statuses = []
        snap = evidence.collect(fake)
        self.assertFalse(any("html_url" in json.dumps(snap) or
                             "Authorization" in json.dumps(snap) for _ in [None]))
        self.assertEqual(set(snap["deploy"]["runs"][0]), {
            "id", "run_attempt", "head_sha", "head_branch", "event", "status", "conclusion"})

    def test_existing_failure_bypasses_unavailable_run_history(self):
        fake = FixtureGitHub()
        fake.fail_endpoint = (
            f"/actions/workflows/{evidence.WORKFLOWS['deploy']}/runs?"
            f"head_sha={S}&branch=main&per_page=100&page=1"
        )
        self.assertEqual(evidence.observe(fake)["decision"], "NOOP")
        self.assertNotIn(fake.fail_endpoint, fake.calls)

    def test_hosted_observer_permissions_are_get_only(self):
        workflow = (
            ROOT / ".github/workflows/zcloud-recovery-retry-reference-20261013.yml"
        ).read_text()
        self.assertIn("github-observe-readonly:", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("statuses: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("actions: write", workflow)
        self.assertNotIn("statuses: write", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("systemctl", workflow)
        self.assertNotIn("POST", workflow)

    def test_network_observer_static_safe_policy(self):
        code = (ROOT / "scripts/zcloud_recovery_github_evidence_20261013.py").read_text()
        for forbidden in ("method=\"POST\"", "/dispatches", "sqlite3", "systemctl",
                          "os.system", "subprocess", "runner-control"):
            self.assertNotIn(forbidden, code)
        self.assertIn("ProxyHandler({})", code)
        self.assertIn("DenyRedirects()", code)


if __name__ == "__main__":
    unittest.main()
