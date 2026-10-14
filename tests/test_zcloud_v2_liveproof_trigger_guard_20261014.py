"""Fail-closed review of the owner-operated Worker-v2 live proof's trigger identity.

Hosted PR tests validate their immutable head without reading the VPS.
Manual proof requires the initiating AND rerunning account to be owner, a
current-main SHA, and independent explicit confirmation.
"""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          ".github/workflows/zcloud-v2-live-proof.yml")


def manual_eligible(event, repository, ref, actor, triggering_actor,
                    submitted_sha, confirmed_sha, remote_main_sha):
    return (
        event == "workflow_dispatch"
        and repository == "Zennay/zCloud"
        and ref == "refs/heads/main"
        and actor == "Zennay"
        and triggering_actor == "Zennay"
        and type(submitted_sha) is str
        and len(submitted_sha) == 40
        and all(char in "0123456789abcdef" for char in submitted_sha)
        and submitted_sha == confirmed_sha == remote_main_sha
    )


class V2LiveproofTriggerGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.hosted = cls.source.split("  validate:\n", 1)[1].split(
            "  prove:\n", 1
        )[0]
        cls.live = cls.source.split("  prove:\n", 1)[1]
        cls.gate = cls.live.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
        cls.good = ("workflow_dispatch", "Zennay/zCloud",
                    "refs/heads/main", "Zennay", "Zennay",
                    "a" * 40, "a" * 40, "a" * 40)

    def test_hosted_pr_checks_exact_head_with_no_credentials(self):
        for marker in (
            "github.event_name == 'pull_request'",
            "runs-on: ubuntu-latest",
            "ref: ${{ github.event.pull_request.head.sha }}",
            "EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}",
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            "persist-credentials: false",
            "tests.test_zcloud_v2_liveproof_trigger_guard_20261014",
        ):
            self.assertIn(marker, self.hosted)
        self.assertNotIn("self-hosted", self.hosted)

    def test_live_job_only_manual_trusted_main_and_both_actors(self):
        for marker in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
        ):
            self.assertIn(marker, self.gate)
        self.assertNotIn("||", self.gate)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.live)

    def test_runtime_actor_current_main_and_confirmation_guards(self):
        before = self.live.split(
            "- name: Trust preflight before reading live worker/SQLite state", 1
        )[1].split("- name: Check live worker heartbeats", 1)[0]
        for marker in (
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            "scripts/zcloud_vps_runner_guard.py --json",
        ):
            self.assertIn(marker, before)

    def test_trusted_fresh_owner_manual_is_eligible(self):
        self.assertTrue(manual_eligible(*self.good))

    def test_untrusted_or_replayed_actor_denied(self):
        for idx in (3, 4):
            value = list(self.good); value[idx] = "someone"
            self.assertFalse(manual_eligible(*value))

    def test_pr_branch_or_fork_denied(self):
        for idx, bad in ((0, "pull_request"), (0, "push"),
                         (1, "attacker/zCloud"), (2, "refs/heads/feature")):
            value = list(self.good); value[idx] = bad
            with self.subTest(idx=idx, bad=bad):
                self.assertFalse(manual_eligible(*value))

    def test_stale_malformed_or_wrong_confirmation_denied(self):
        for idx, bad in ((5, "b" * 40), (5, "BAD"), (5, "A" * 40),
                         (6, "b" * 40), (7, "b" * 40)):
            value = list(self.good); value[idx] = bad
            with self.subTest(idx=idx, bad=bad):
                self.assertFalse(manual_eligible(*value))

    def test_no_auto_live_evidence_or_prod_mutation(self):
        events = self.source.split("\npermissions:", 1)[0]
        self.assertNotIn("\n  push:\n", events)
        self.assertIn("\n  workflow_dispatch:\n", events)
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("cancel-in-progress: false", self.source)
        for forbidden in ("sudo ", "systemctl restart", "systemctl stop",
                          "git push", "actions: write", "contents: write"):
            self.assertNotIn(forbidden, self.live)


if __name__ == "__main__":
    unittest.main()
