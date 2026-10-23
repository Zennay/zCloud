"""Source regression for safe FTMO runner diagnostics on GitHub Actions.

A pull request can validate source on GitHub-hosted CI only. Access to the
permanent VPS diagnostic remains a separate owner-only, exact-main dispatch.
No service restart, GitHub action or remote runner is executed by these tests.
"""
from pathlib import Path
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / (
    ".github/workflows/zcloud-ftmo-research-runner-diagnostic.yml"
)


def admission(event: str, repository: str, ref: str, actor: str,
              triggering_actor: str, submitted_sha: str,
              current_main_sha: str) -> bool:
    return (
        event == "workflow_dispatch"
        and repository == "Zennay/zCloud"
        and ref == "refs/heads/main"
        and actor == "Zennay"
        and triggering_actor == "Zennay"
        and len(submitted_sha) == 40
        and all(x in "0123456789abcdef" for x in submitted_sha)
        and submitted_sha == current_main_sha
    )


class FtmoDiagnosticDispatchIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.hosted = cls.source.split("  hosted-source:\n", 1)[1].split(
            "  diagnose:\n", 1
        )[0]
        cls.live = cls.source.split("  diagnose:\n", 1)[1]
        cls.gate = cls.live.split("    if: >-\n", 1)[1].split(
            "    runs-on:", 1
        )[0]
        cls.guard = cls.live.split(
            "- name: Prove trusted permanent zCloud runner", 1
        )[1].split("- name: Run focused diagnostic regressions", 1)[0]
        cls.valid = (
            "workflow_dispatch", "Zennay/zCloud", "refs/heads/main",
            "Zennay", "Zennay", "a" * 40, "a" * 40,
        )

    def test_prs_have_no_self_hosted_execution(self):
        self.assertIn("runs-on: ubuntu-latest", self.hosted)
        self.assertNotIn("self-hosted", self.hosted)
        self.assertIn("github.event_name == 'pull_request'", self.hosted)
        self.assertIn("github.actor == 'Zennay'", self.hosted)
        self.assertIn("github.triggering_actor == 'Zennay'", self.hosted)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.hosted,
        )

    def test_hosted_regression_checks_exact_head_without_secrets(self):
        for marker in (
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "persist-credentials: false",
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            "tests.test_zcloud_ftmo_research_runner_diagnostic",
            "tests.test_zcloud_ftmo_research_runner_diagnostic_workflow",
            "tests.test_ftmo_diagnostic_dispatch_isolation_20261014",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.hosted)

    def test_live_job_exclusive_manual_trusted_main(self):
        for marker in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.gate)
        self.assertNotIn("||", self.gate)
        self.assertNotIn("github.event_name != 'pull_request'", self.gate)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.live)

    def test_live_checkout_and_remote_main_bound_before_diagnostic(self):
        for marker in (
            "ref: ${{ github.sha }}",
            "persist-credentials: false",
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
            with self.subTest(marker=marker):
                self.assertIn(marker, self.live)
        self.assertLess(
            self.live.index('test "$current_main" = "$EXPECTED_SHA"'),
            self.live.index("python3 scripts/zcloud_ftmo_research_runner_diagnostic.py"),
        )

    def test_valid_fresh_manual_owner_admitted(self):
        self.assertTrue(admission(*self.valid))

    def test_fork_branch_and_bad_event_denied(self):
        for index, replacement in (
            (0, "pull_request"), (0, "push"),
            (1, "someone/zCloud"), (2, "refs/heads/feature"),
        ):
            args = list(self.valid)
            args[index] = replacement
            with self.subTest(index=index, replacement=replacement):
                self.assertFalse(admission(*args))

    def test_untrusted_actor_and_rerun_denied(self):
        for index in (3, 4):
            args = list(self.valid)
            args[index] = "someone"
            with self.subTest(index=index):
                self.assertFalse(admission(*args))

    def test_stale_invalid_revision_denied(self):
        for index, replacement in (
            (5, "b" * 40), (5, "invalid"), (5, "A" * 40), (6, "b" * 40),
        ):
            args = list(self.valid)
            args[index] = replacement
            with self.subTest(index=index, replacement=replacement):
                self.assertFalse(admission(*args))

    def test_no_service_or_runner_mutation_permission(self):
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        for bad in ("sudo ", "systemctl restart", "systemctl start",
                    "systemctl stop", "git push", "gh api",
                    "actions: write", "contents: write"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, self.source)


if __name__ == "__main__":
    unittest.main()
