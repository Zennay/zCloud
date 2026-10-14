"""Offline tests for fail-closed source admission in worker-private diagnostics.

The exact Bash admission code runs in a fake local environment only. It never
checks out code, calls GitHub, or reads worker prompts and private VPS data.
"""
import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-worker-prompt-stop-20261013.yml"


def admission_source(source: str) -> str:
    segment = source.split("  trusted-pr-source:\n", 1)[1].split(
        "  hosted-policy-proof:\n", 1
    )[0]
    return textwrap.dedent(segment.split("        run: |\n", 1)[1])


def simulated_admission(source: str, **overrides: str) -> int:
    env = os.environ.copy()
    env.update(
        EVENT_KIND="pull_request", EXPECTED_REPO="Zennay/zCloud",
        HEAD_REPO="Zennay/zCloud", BASE_REPO="Zennay/zCloud",
        GITHUB_BASE_REF="main", ATTEMPT="1",
    )
    env.update(overrides)
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + admission_source(source)],
        env=env, capture_output=True, text=True, timeout=3, check=False,
    )
    return result.returncode


def static_failures(source: str) -> list[str]:
    problems = []
    if "\n  push:\n" in source or "\n  schedule:\n" in source:
        problems.append("no_automatic_live_trigger")
    if source.count("  trusted-pr-source:\n") != 1:
        problems.append("trusted_admission_job")
    first = source.split("  trusted-pr-source:\n", 1)[1].split(
        "  hosted-policy-proof:\n", 1
    )[0] if "  trusted-pr-source:\n" in source else ""
    hosted = source.split("  hosted-policy-proof:\n", 1)[1].split(
        "  owner-vps-inspection:\n", 1
    )[0] if "  hosted-policy-proof:\n" in source else ""
    live = source.split("  owner-vps-inspection:\n", 1)[1] if "  owner-vps-inspection:\n" in source else ""
    if "if: github.event_name == 'pull_request'" not in first:
        problems.append("admission_pr_only")
    if "runs-on: ubuntu-latest" not in first or "actions/checkout" in first:
        problems.append("admission_uses_no_contributor_code")
    if 'test "$ATTEMPT" = 1' not in first:
        problems.append("admission_first_attempt_only")
    if "needs: trusted-pr-source" not in hosted:
        problems.append("hosted_requires_admission")
    if "runs-on: ubuntu-latest" not in hosted:
        problems.append("hosted_runner")
    for expression in (
        "github.repository == 'Zennay/zCloud'",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "github.event.pull_request.base.repo.full_name == github.repository",
    ):
        if expression not in hosted:
            problems.append("hosted_source_guard:" + expression)
    for expression in (
        "github.event_name == 'workflow_dispatch'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'Zennay'",
        "github.triggering_actor == 'Zennay'",
        "inputs.confirm_sha == github.sha",
        "inputs.approve_readonly_diagnostic == true",
    ):
        if expression not in live:
            problems.append("live_authority:" + expression)
    if "needs: trusted-pr-source" in live:
        problems.append("manual_does_not_depend_on_skipped_pr")
    if "tests.test_deployops_worker_diagnostic_fork_admission_20261014_w7" not in hosted:
        problems.append("source_test_executed")
    if "tests/test_deployops_worker_diagnostic_fork_admission_20261014_w7.py" not in source:
        problems.append("test_path_triggered")
    return problems


class WorkerDiagnosticForkAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_canonical_workflow_contract(self):
        self.assertEqual([], static_failures(self.source))

    def test_owner_same_repository_pr_is_accepted(self):
        self.assertEqual(0, simulated_admission(self.source))

    def test_fork_source_or_base_does_not_get_green_skip(self):
        for overrides in (
            {"HEAD_REPO": "untrusted/fork"},
            {"HEAD_REPO": ""},
            {"BASE_REPO": "untrusted/fork"},
            {"EXPECTED_REPO": "untrusted/fork"},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(0, simulated_admission(self.source, **overrides))

    def test_manual_push_branch_and_rerun_denied_from_pr_admission(self):
        for overrides in (
            {"EVENT_KIND": "workflow_dispatch"},
            {"EVENT_KIND": "push"},
            {"GITHUB_BASE_REF": "develop"},
            {"GITHUB_BASE_REF": ""},
            {"ATTEMPT": "2"},
            {"ATTEMPT": ""},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(0, simulated_admission(self.source, **overrides))

    def test_no_automatic_sensitive_worker_probe(self):
        for change in (
            "  push:\n    branches: [main]\n",
            "  schedule:\n    - cron: '0 * * * *'\n",
        ):
            self.assertIn("no_automatic_live_trigger", static_failures(self.source + "\n" + change))

    def test_hosted_job_cannot_bypass_pr_admission(self):
        altered = self.source.replace("    needs: trusted-pr-source", "    needs: []", 1)
        self.assertIn("hosted_requires_admission", static_failures(altered))

    def test_fork_source_cannot_execute_in_admission_job(self):
        changed = self.source.replace(
            "    timeout-minutes: 3",
            "    timeout-minutes: 3\n    uses: actions/checkout@v4", 1
        )
        self.assertIn("admission_uses_no_contributor_code", static_failures(changed))

    def test_manual_readonly_vps_flow_stays_separate(self):
        self.assertEqual([], static_failures(self.source))
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.source)
        self.assertNotIn("\n  push:\n", self.source)


if __name__ == "__main__":
    unittest.main()
