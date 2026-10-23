"""Deny stale or non-owner manual memory isolation proof on the production VPS.

Offline fixtures only; tests do not access GitHub, VPS, browser or SQLite.
"""
from pathlib import Path
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-autonomy-memory-isolation.yml")


def manual_admitted(event: str, repository: str, ref: str,
                    actor: str, triggering_actor: str,
                    dispatched_sha: str, current_main_sha: str) -> bool:
    return (
        event == "workflow_dispatch"
        and repository == "Zennay/zCloud"
        and ref == "refs/heads/main"
        and actor == "Zennay"
        and triggering_actor == "Zennay"
        and len(dispatched_sha) == 40
        and dispatched_sha == current_main_sha
    )


class AutonomyManualMainAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.hosted = cls.text.split("  validate-source:\n", 1)[1].split("  verify:\n", 1)[0]
        cls.verify = cls.text.split("  verify:\n", 1)[1]
        cls.gate = cls.verify.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
        cls.guard = cls.text.split("- name: Verify exact candidate and permanent runner", 1)[1].split(
            "- name: Record live memory admission", 1
        )[0]
        cls.good = ("workflow_dispatch", "Zennay/zCloud", "refs/heads/main",
                    "Zennay", "Zennay", "a" * 40, "a" * 40)

    def test_manual_gate_requires_full_repository_and_main_identity(self):
        for clause in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, self.gate)
        self.assertNotIn("github.event_name == 'pull_request'", self.gate)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository",
                      self.hosted)

    def test_pull_requests_have_hosted_only_source_validation(self):
        self.assertIn("runs-on: ubuntu-latest", self.hosted)
        self.assertNotIn("runs-on: [self-hosted", self.hosted)
        self.assertIn("github.event_name == 'pull_request'", self.hosted)
        self.assertIn("github.actor == 'Zennay'", self.hosted)
        self.assertIn("github.triggering_actor == 'Zennay'", self.hosted)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.hosted)
        self.assertIn("persist-credentials: false", self.hosted)
        self.assertIn("tests.test_autonomy_memory_manual_admission_20261014", self.hosted)

    def test_live_self_hosted_job_requires_manual_owner_and_main(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.verify)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.gate)
        self.assertNotIn("||", self.gate)
        self.assertNotIn("github.event_name == 'pull_request'", self.verify)
        self.assertIn("EXPECTED_ZCLOUD_SHA: ${{ github.sha }}", self.verify)
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", self.verify)

    def test_manual_runtime_rejects_stale_sha_before_memory_read(self):
        for clause in (
            'test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
            'test "$GITHUB_REF" = "refs/heads/main"',
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            "git ls-remote --exit-code origin refs/heads/main",
            'test "$current_main" = "$EXPECTED_ZCLOUD_SHA"',
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, self.guard)
        self.assertLess(self.text.index("Verify exact candidate and permanent runner"),
                        self.text.index("Record live memory admission"))

    def test_owner_fresh_main_admitted(self):
        self.assertTrue(manual_admitted(*self.good))

    def test_branch_and_fork_dispatch_denied(self):
        for index, value in ((1, "someone/zCloud"), (2, "refs/heads/feature")):
            row = list(self.good)
            row[index] = value
            with self.subTest(index=index):
                self.assertFalse(manual_admitted(*row))

    def test_untrusted_actor_or_rerun_denied(self):
        for index in (3, 4):
            row = list(self.good)
            row[index] = "other-user"
            with self.subTest(index=index):
                self.assertFalse(manual_admitted(*row))

    def test_stale_or_invalid_sha_denied(self):
        for index, value in ((5, "b" * 40), (5, "bad"), (6, "b" * 40)):
            row = list(self.good)
            row[index] = value
            with self.subTest(index=index):
                self.assertFalse(manual_admitted(*row))

    def test_unexpected_event_denied(self):
        row = list(self.good)
        row[0] = "pull_request"
        self.assertFalse(manual_admitted(*row))

    def test_no_new_mutation_commands_or_credentials(self):
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
                      self.text)
        for forbidden in ("sudo ", "systemctl ", "git push", "INSERT INTO",
                          "UPDATE ", "DELETE FROM"):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
