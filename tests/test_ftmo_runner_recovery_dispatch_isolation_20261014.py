"""Deny PR or arbitrary dispatch of the permanent-VPS FTMO recovery preflight.

This source-only suite never starts/stops runners, touches queue/SQLite, or
performs a live recovery. The preflight result itself cannot authorize mutation.
"""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-ftmo-runner-recovery-preflight.yml")


def allowed_manual(event, repository, ref, actor, triggering_actor, sha, current):
    return (
        event == "workflow_dispatch"
        and repository == "Zennay/zCloud"
        and ref == "refs/heads/main"
        and actor == "Zennay"
        and triggering_actor == "Zennay"
        and isinstance(sha, str)
        and len(sha) == 40
        and all(c in "0123456789abcdef" for c in sha)
        and sha == current
    )


class RecoveryPreflightDispatchIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.hosted = cls.source.split("  hosted-source:\n", 1)[1].split(
            "  preflight:\n", 1
        )[0]
        cls.live = cls.source.split("  preflight:\n", 1)[1]
        cls.gate = cls.live.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
        cls.guard = cls.live.split(
            "- name: Prove trusted permanent zCloud runner", 1
        )[1].split("- name: Run focused recovery-preflight regressions", 1)[0]
        cls.good = ("workflow_dispatch", "Zennay/zCloud", "refs/heads/main",
                    "Zennay", "Zennay", "a" * 40, "a" * 40)

    def test_pr_checks_only_on_hosted_runner(self):
        self.assertIn("runs-on: ubuntu-latest", self.hosted)
        self.assertNotIn("self-hosted", self.hosted)
        for s in (
            "github.event_name == 'pull_request'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.event.pull_request.head.repo.full_name == github.repository",
        ):
            self.assertIn(s, self.hosted)

    def test_hosted_exact_head_and_no_credentials(self):
        for s in (
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "persist-credentials: false",
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            "tests.test_zcloud_ftmo_research_runner_diagnostic",
            "tests.test_zcloud_ftmo_runner_recovery_preflight",
            "tests.test_ftmo_runner_recovery_dispatch_isolation_20261014",
        ):
            self.assertIn(s, self.hosted)

    def test_vps_job_exclusive_manual_current_main(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.live)
        for s in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
        ):
            self.assertIn(s, self.gate)
        self.assertNotIn("||", self.gate)
        self.assertNotIn("github.event_name != 'pull_request'", self.gate)

    def test_runtime_same_job_live_evidence_after_trust(self):
        for s in (
            "ref: ${{ github.sha }}",
            "EXPECTED_SHA: ${{ github.sha }}",
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
            'test "$GITHUB_REF" = "refs/heads/main"',
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            "git ls-remote --exit-code origin refs/heads/main",
            'test "$current_main" = "$EXPECTED_SHA"',
            "scripts/zcloud_vps_runner_guard.py --json",
        ):
            self.assertIn(s, self.live)
        self.assertLess(
            self.live.index('test "$current_main" = "$EXPECTED_SHA"'),
            self.live.index("python3 scripts/zcloud_ftmo_research_runner_diagnostic.py >"),
        )
        self.assertIn("--same-job-live-evidence", self.live)
        self.assertIn('assert payload["mutation_authorized"] is False', self.live)

    def test_owner_manual_exact_main_eligible(self):
        self.assertTrue(allowed_manual(*self.good))

    def test_pr_push_fork_and_other_branch_denied(self):
        for idx, invalid in (
            (0, "pull_request"), (0, "push"),
            (1, "attacker/zCloud"), (2, "refs/heads/feature"),
        ):
            args = list(self.good); args[idx] = invalid
            with self.subTest(idx=idx, invalid=invalid):
                self.assertFalse(allowed_manual(*args))

    def test_untrusted_actor_or_rerun_denied(self):
        for idx in (3, 4):
            args = list(self.good); args[idx] = "untrusted"
            with self.subTest(idx=idx):
                self.assertFalse(allowed_manual(*args))

    def test_malformed_or_replayed_head_denied(self):
        for idx, invalid in ((5, "x"), (5, "B"*40), (5, "b"*40), (6, "b"*40)):
            args = list(self.good); args[idx] = invalid
            with self.subTest(idx=idx, invalid=invalid):
                self.assertFalse(allowed_manual(*args))

    def test_no_privileged_actions_or_auto_cancellation(self):
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        for token in ("sudo ", "systemctl start", "systemctl restart",
                      "systemctl stop", "pkill ", "git push", "gh api",
                      "actions: write", "contents: write"):
            self.assertNotIn(token, self.source)


if __name__ == "__main__":
    unittest.main()
