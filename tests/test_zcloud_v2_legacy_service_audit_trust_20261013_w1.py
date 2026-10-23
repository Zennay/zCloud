"""Deny-first source contracts for the v2 legacy service inventory trust boundary."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-v2-legacy-service-audit.yml"


class LegacyServiceAuditTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        assert cls.text.startswith("name: zCloud v2 legacy worker service audit")

    @staticmethod
    def job(text, name):
        fragment = text.split("jobs:\n", 1)[1]
        start = f"  {name}:\n"
        if start not in fragment:
            raise AssertionError(f"missing job: {name}")
        part = fragment.split(start, 1)[1]
        return re.split(r"\n  [a-zA-Z0-9_-]+:\n", part, maxsplit=1)[0]

    def test_pr_is_hosted_only(self):
        pr = self.job(self.text, "validate")
        self.assertIn("github.event_name == 'pull_request'", pr)
        self.assertIn("runs-on: ubuntu-latest", pr)
        self.assertNotIn("self-hosted", pr)
        self.assertNotIn("workflow_dispatch", pr)
        self.assertNotIn("systemctl ", pr)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_v2_legacy_service_audit_trust_20261013_w1", pr)

    def test_vps_job_requires_manual_canonical_main_and_actor(self):
        vps = self.job(self.text, "inspect")
        self.assertIn("github.event_name == 'workflow_dispatch'", vps)
        self.assertIn("github.ref == 'refs/heads/main'", vps)
        self.assertIn("github.repository == 'Zennay/zCloud'", vps)
        self.assertIn("github.actor == 'Zennay'", vps)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", vps)
        self.assertNotIn("needs: validate", vps)

    def test_exact_sha_checkout_and_no_credentials_in_both_jobs(self):
        for name in ("validate", "inspect"):
            with self.subTest(job=name):
                job = self.job(self.text, name)
                self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", job)
                expected_ref = ("github.event.pull_request.head.sha"
                                if name == "validate" else "github.sha")
                self.assertIn("ref: ${{ " + expected_ref + " }}", job)
                self.assertIn("persist-credentials: false", job)
                actual = ('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'
                          if name == "validate" else
                          'test "$(git rev-parse HEAD)" = "$CONFIRMED_SHA"')
                self.assertIn(actual, job)

    def test_permanent_vps_gate_precedes_any_live_probes(self):
        job = self.job(self.text, "inspect")
        first = job.index("- name: Enforce exact commit and permanent VPS identity")
        second = job.index("- name: Read-only bounded worker service inventory")
        self.assertLess(first, second)
        gate = job[first:second]
        for claim in (
            "test \"$GITHUB_REPOSITORY\" = 'Zennay/zCloud'",
            "test \"$GITHUB_REF\" = 'refs/heads/main'",
            "test \"$(hostname)\" = 'vps-bb300bba'",
            "test \"$(id -un)\" = 'ubuntu'",
            "python3 scripts/zcloud_vps_runner_guard.py --json",
        ):
            self.assertIn(claim, gate)
        self.assertNotIn("if: always()", job)

    def test_pr_changing_own_files_triggers_hosted_validation(self):
        header = self.text.split("\npermissions:", 1)[0]
        self.assertIn("  pull_request:", header)
        self.assertIn("branches: [main]", header)
        self.assertIn(".github/workflows/zcloud-v2-legacy-service-audit.yml", header)
        self.assertIn("tests/test_zcloud_v2_legacy_service_audit_trust_20261013_w1.py", header)
        self.assertIn("  workflow_dispatch:", header)
        self.assertNotIn("  workflow_run:", header)

    def test_least_privilege_no_mutation_or_secret_exposure(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        job = self.job(self.text, "inspect")
        probe = job.split("- name: Read-only bounded worker service inventory", 1)[1]
        self.assertIn("ps -eo pid=,ppid=,comm=", probe)
        self.assertNotIn("ps -eo pid=,ppid=,comm=,args=", probe)
        for forbidden in (
            "sudo ", "systemctl restart", "systemctl stop", "systemctl disable",
            "systemctl enable", "kill -", "pkill", "curl -X", "tabs.remove",
            "POST", "pip install", "GITHUB_TOKEN",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, probe)

    def test_no_self_hosted_pr_fallback_or_condition_bypass(self):
        self.assertEqual(self.text.count("runs-on: [self-hosted, zcloud, vps]"), 1)
        self.assertNotIn("|| github.event_name == 'pull_request'", self.job(self.text, "inspect"))
        self.assertNotIn("pull_request.head.repo", self.job(self.text, "inspect"))
        self.assertIn("cancel-in-progress: false", self.text)

    def test_bounded_diagnostics(self):
        job = self.job(self.text, "inspect")
        for cap in ("head -n 5", "head -n 65", "head -n 25"):
            self.assertIn(cap, job)
        self.assertIn("LIVE_EXTENSION_BACKGROUND_FILE_COUNT=", job)
        self.assertNotIn("LIVE_EXTENSION_BACKGROUND_FILES=", job)
        self.assertIn("NO_PROCESS_OR_TAB_MUTATION=1", job)


    def test_manual_probe_denies_moved_main_before_any_live_read(self):
        vps = self.job(self.text, "inspect")
        gate = vps.split("- name: Enforce exact commit and permanent VPS identity", 1)[1].split("- name: Read-only bounded worker service inventory", 1)[0]
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", gate)
        self.assertIn('test "$current_main" = "$CONFIRMED_SHA"', gate)
        self.assertLess(gate.index("git ls-remote"), gate.index("python3 scripts/zcloud_vps_runner_guard.py"))

    def test_trigger_matrix_deny_untrusted_events_and_manual_drift(self):
        vps = self.job(self.text, "inspect")
        guard = vps.split("    if: >-\n", 1)[1].split("\n    runs-on:", 1)[0]
        self.assertEqual([line.strip() for line in guard.splitlines()], [
            "github.event_name == 'workflow_dispatch' &&",
            "github.ref == 'refs/heads/main' &&",
            "github.repository == 'Zennay/zCloud' &&",
            "github.actor == 'Zennay' &&",
            "github.triggering_actor == 'Zennay' &&",
            "inputs.confirm_sha == github.sha",
        ])
        sha = "a" * 40
        cases = [
            ("workflow_dispatch", "refs/heads/main", "Zennay/zCloud", "Zennay", "Zennay", sha, True),
            ("pull_request", "refs/pull/1313/merge", "Zennay/zCloud", "Zennay", "Zennay", sha, False),
            ("push", "refs/heads/main", "Zennay/zCloud", "Zennay", "Zennay", sha, False),
            ("workflow_dispatch", "refs/heads/feature", "Zennay/zCloud", "Zennay", "Zennay", sha, False),
            ("workflow_dispatch", "refs/heads/main", "fork/zCloud", "Zennay", "Zennay", sha, False),
            ("workflow_dispatch", "refs/heads/main", "Zennay/zCloud", "outside", "Zennay", sha, False),
            ("workflow_dispatch", "refs/heads/main", "Zennay/zCloud", "Zennay", "outside", sha, False),
            ("workflow_dispatch", "refs/heads/main", "Zennay/zCloud", "Zennay", "Zennay", "b" * 40, False),
        ]
        for event, ref, repo, actor, rerun_actor, confirmed, expected in cases:
            with self.subTest(event=event, ref=ref, repo=repo, actor=actor, rerun=rerun_actor):
                result = (event == "workflow_dispatch" and
                          ref == "refs/heads/main" and
                          repo == "Zennay/zCloud" and
                          actor == rerun_actor == "Zennay" and
                          confirmed == sha)
                self.assertEqual(result, expected)

    def test_manual_dispatch_requires_explicit_sha_input(self):
        header = self.text.split("\npermissions:", 1)[0]
        self.assertIn("confirm_sha:", header)
        self.assertIn("type: string", header)
        self.assertIn("required: true", header)
        gate = self.job(self.text, "inspect")
        self.assertIn("inputs.confirm_sha == github.sha", gate)

    def test_manual_guard_fails_closed_before_live_observation(self):
        gate = self.job(self.text, "inspect").split(
            "- name: Enforce exact commit and permanent VPS identity", 1
        )[1].split("- name: Read-only bounded worker service inventory", 1)[0]
        for required in (
            "test \"$GITHUB_TRIGGERING_ACTOR\" = 'Zennay'",
            '[[ "$CONFIRMED_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            'test "$(git rev-parse HEAD)" = "$CONFIRMED_SHA"',
            "timeout 15 git ls-remote --exit-code origin refs/heads/main",
            'test "$current_main" = "$CONFIRMED_SHA"',
        ):
            self.assertIn(required, gate)

    def test_hosted_pr_validates_exact_head_not_synthetic_merge_sha(self):
        validate = self.job(self.text, "validate")
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", validate)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", validate)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', validate)
        self.assertNotIn("ref: ${{ github.sha }}", validate)



if __name__ == "__main__":
    unittest.main()
