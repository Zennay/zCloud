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
                    submitted_sha, confirmed_sha, remote_main_sha, run_attempt=1):
    return (
        event == "workflow_dispatch"
        and repository == "Zennay/zCloud"
        and ref == "refs/heads/main"
        and actor == "Zennay"
        and triggering_actor == "Zennay"
        and type(run_attempt) is int
        and run_attempt == 1
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
            "github.run_attempt == 1",
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
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            "scripts/zcloud_vps_runner_guard.py --json",
        ):
            self.assertIn(marker, before)

    def test_replay_attempt_is_rejected_without_vps_access(self):
        for attempt in (0, 2, 3, -1, "1", True, 1.0, None):
            with self.subTest(attempt=attempt):
                self.assertFalse(manual_eligible(*self.good, run_attempt=attempt))

    def test_main_rechecked_directly_before_live_observation(self):
        observation = self.live.split(
            "- name: Check live worker heartbeats on read-only SQLite and localhost", 1
        )[1]
        proof = observation.split("python3 scripts/zcloud_v2_live_proof_freshness_20261013_w1.py", 1)
        self.assertEqual(len(proof), 2)
        for marker in (
            'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
        ):
            self.assertIn(marker, proof[0])
        self.assertIn("timeout 15 git ls-remote --exit-code origin refs/heads/main", proof[1])

    def test_real_bash_preflight_denies_replay_and_stale_main(self):
        """Execute extracted Bash admission with mocked identity, never contacting VPS."""
        import os
        import subprocess
        import tempfile
        import textwrap

        preflight = self.live.split(
            "- name: Trust preflight before reading live worker/SQLite state", 1
        )[1].split("- name: Check live worker heartbeats", 1)[0]
        script = textwrap.dedent(preflight.split("run: |\n", 1)[1])
        good_sha = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            commands = {
                "git": """#!/bin/sh
if [ "$1" = "rev-parse" ]; then echo "$GITHUB_SHA"; exit 0; fi
if [ "$1" = "ls-remote" ]; then echo "$MOCK_REMOTE_MAIN refs/heads/main"; exit 0; fi
exit 3
""",
                "id": "#!/bin/sh\necho ubuntu\n",
                "hostname": "#!/bin/sh\necho vps-bb300bba\n",
                "python3": '#!/bin/sh\necho guard-called >> "$MOCK_GUARD_LOG"\n',
            }
            for name, body in commands.items():
                exe = Path(tmp) / name
                exe.write_text(body, encoding="utf-8")
                exe.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "PATH": tmp + os.pathsep + env.get("PATH", ""),
                "CONFIRMED_SHA": good_sha,
                "READONLY_APPROVAL": "true",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_SHA": good_sha,
                "MOCK_REMOTE_MAIN": good_sha,
                "GITHUB_REPOSITORY": "Zennay/zCloud",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_ACTOR": "Zennay",
                "GITHUB_TRIGGERING_ACTOR": "Zennay",
                "GITHUB_RUN_ATTEMPT": "1",
                "MOCK_GUARD_LOG": str(Path(tmp) / "guard.log"),
            })
            log = Path(env["MOCK_GUARD_LOG"])

            def execute(overrides):
                log.unlink(missing_ok=True)
                result = subprocess.run(
                    ["bash", "-c", script], env={**env, **overrides},
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode, log.exists()

            self.assertEqual(execute({}), (0, True))
            for overrides in (
                {"READONLY_APPROVAL": "false"},
                {"READONLY_APPROVAL": ""},
                {"GITHUB_EVENT_NAME": "push"},
                {"GITHUB_RUN_ATTEMPT": "2"},
                {"GITHUB_RUN_ATTEMPT": ""},
                {"GITHUB_TRIGGERING_ACTOR": "another"},
                {"GITHUB_ACTOR": "another"},
                {"GITHUB_REPOSITORY": "fork/zCloud"},
                {"GITHUB_REF": "refs/heads/feature"},
                {"CONFIRMED_SHA": "b" * 40},
                {"MOCK_REMOTE_MAIN": "b" * 40},
                {"MOCK_REMOTE_MAIN": ""},
            ):
                with self.subTest(overrides=overrides):
                    code, guard_called = execute(overrides)
                    self.assertNotEqual(code, 0)
                    self.assertFalse(guard_called)

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
