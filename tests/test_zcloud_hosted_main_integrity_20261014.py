"""Hosted-only continuity contract: source green is never live production proof."""
import unittest
from pathlib import Path
from scripts.zcloud_hosted_main_integrity_20261014 import admission

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-hosted-main-source-verification.yml"
SHA = "a" * 40


def good(event="schedule"):
    return dict(event=event, repository="Zennay/zCloud", ref="refs/heads/main",
                actor="github-actions[bot]", triggering_actor="github-actions[bot]",
                attempt="1", expected_sha=SHA, checkout_sha=SHA,
                main_sha=SHA, head_repo="Zennay/zCloud", stage="before")


class HostedMainIntegrityTests(unittest.TestCase):
    def test_hourly_main_source_only_before_and_after(self):
        for stage in ("before", "after"):
            value = admission(**dict(good(), stage=stage))
            self.assertTrue(value["source_verified"])
            for key in ("production_verified", "runner_health_verified", "authorizes_mutation"):
                self.assertFalse(value[key])

    def test_owner_manual_both_actors(self):
        sample = dict(good("workflow_dispatch"), actor="Zennay", triggering_actor="Zennay")
        self.assertTrue(admission(**sample)["source_verified"])
        for who in ("actor", "triggering_actor"):
            self.assertFalse(admission(**dict(sample, **{who:"stranger"}))["source_verified"])

    def test_fail_closed_moving_main_replay_fork_unknown_events(self):
        for mutation in (
            {"repository":"fork/zCloud"}, {"ref":"refs/heads/feature"},
            {"expected_sha":"b"*40}, {"checkout_sha":"b"*40}, {"main_sha":"b"*40},
            {"main_sha":None}, {"main_sha":""}, {"expected_sha":"A"*40},
            {"attempt":"2"}, {"attempt":1}, {"attempt":True}, {"stage":"other"},
            {"event":"push"}, {"event":"workflow_run"}, {"expected_sha":"x"*40},
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(admission(**dict(good(), **mutation))["source_verified"])

    def test_automerge_workflow_run_only_on_new_main(self):
        old = "b" * 40
        valid = dict(good("workflow_run"), upstream_sha=old)
        value = admission(**valid)
        self.assertTrue(value["source_verified"])
        self.assertFalse(value["production_verified"])
        self.assertFalse(value["authorizes_mutation"])
        for upstream in (None, "", "0", "x"*40, "A"*40, SHA, True, 42):
            with self.subTest(upstream=upstream):
                self.assertFalse(admission(**dict(valid, upstream_sha=upstream))["source_verified"])
        for change in ({"main_sha":old}, {"checkout_sha":old},
                       {"ref":"refs/heads/other"}, {"attempt":"2"},
                       {"repository":"other/zCloud"}):
            self.assertFalse(admission(**dict(valid, **change))["source_verified"])

    def test_pr_requires_immutable_same_repo_head(self):
        sample = dict(good("pull_request"), ref="refs/pull/1401/merge")
        self.assertTrue(admission(**sample)["source_verified"])
        for change in ({"head_repo":"fork/zCloud"},
                       {"checkout_sha":"b"*40}, {"ref":"refs/heads/main"}):
            self.assertFalse(admission(**dict(sample, **change))["source_verified"])

    def test_schedule_bypasses_skip_ci_without_deployment_triggers(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  schedule:", source)
        self.assertIn("cron: '23 * * * *'", source)
        self.assertIn("  pull_request:", source)
        self.assertIn("  workflow_dispatch:", source)
        self.assertNotIn("\n  push:\n", source)
        self.assertIn("\n  workflow_run:\n", source)
        self.assertIn("workflows: ['zCloud autonomous safe PR integration']", source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", source)
        self.assertIn("github.event.workflow_run.head_sha != github.sha", source)
        self.assertIn("permissions:\n  contents: read\n", source)
        self.assertEqual(source.count("runs-on: ubuntu-latest"), 1)
        for forbidden in ("runs-on: self-hosted", "runs-on: [self-hosted",
                          "actions: write", "contents: write", "gh workflow run",
                          "systemctl restart", "sudo ", "sqlite3.connect(", "git push "):
            self.assertNotIn(forbidden, source)

    def test_exact_checkout_and_main_reverified_after_full_suite(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("persist-credentials: false", source)
        self.assertIn("github.run_attempt == 1", source)
        self.assertIn("github.triggering_actor == 'Zennay'", source)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", source)
        self.assertEqual(source.count("timeout 15 git ls-remote --exit-code origin refs/heads/main"), 2)
        self.assertLess(source.index("--stage before"), source.index("python3 -m unittest discover -v"))
        self.assertGreater(source.index("--stage after"), source.index("python3 -m unittest discover -v"))
        self.assertIn("if: always()", source)
        self.assertIn("retention-days: 7", source)
        self.assertEqual(source.count("--upstream-sha"), 2)


if __name__ == "__main__":
    unittest.main()
