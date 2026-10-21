from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_self_hosted_trust_no_regression import (
    audit,
    findings_for_text,
)


WORKFLOW = Path(".github/workflows/zcloud-self-hosted-trust-no-regression.yml")


class SelfHostedTrustNoRegressionTests(unittest.TestCase):
    def test_finds_unsafe_self_hosted_trust_signals(self):
        findings = findings_for_text(
            """
name: unsafe
on: [pull_request]
jobs:
  proof:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
"""
        )
        self.assertEqual(findings["generic_self_hosted_runner"], 1)
        self.assertEqual(
            findings["pr_self_hosted_without_owner_same_repo_guard"], 1
        )
        self.assertEqual(findings["floating_checkout_action"], 1)
        self.assertEqual(
            findings["checkout_credentials_not_explicitly_disabled"], 1
        )
        self.assertEqual(findings["checkout_exact_ref_not_evident"], 1)

    def test_hardened_zcloud_vps_job_has_no_findings(self):
        findings = findings_for_text(
            """
name: hardened
on:
  pull_request:
jobs:
  proof:
    if: github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository
    runs-on: [self-hosted, zcloud, vps]
    env:
      EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          ref: ${{ env.EXPECTED_SHA }}
          persist-credentials: false
      - run: python3 scripts/zcloud_vps_runner_guard.py --json
"""
        )
        self.assertEqual(findings, {})

    def test_audit_allows_reduction_but_blocks_new_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "zCloud test"],
                cwd=root,
                check=True,
            )
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            target = workflows / "sample.yml"
            target.write_text(
                """
name: sample
on:
  workflow_dispatch:
jobs:
  proof:
    runs-on: self-hosted
""",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()

            target.write_text(
                """
name: sample
on:
  workflow_dispatch:
jobs:
  proof:
    runs-on: [self-hosted, custom]
""",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "reduce debt"], cwd=root, check=True)
            reduced = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()

            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                self.assertTrue(audit(base, reduced)["ok"])

                target.write_text(
                    """
name: sample
on:
  pull_request:
jobs:
  proof:
    runs-on: [self-hosted, custom]
""",
                    encoding="utf-8",
                )
                subprocess.run(["git", "add", "."], cwd=root, check=True)
                subprocess.run(
                    ["git", "commit", "-qm", "introduce pr debt"],
                    cwd=root,
                    check=True,
                )
                unsafe = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip()
                report = audit(reduced, unsafe)
            finally:
                os.chdir(old_cwd)

            self.assertFalse(report["ok"])
            self.assertEqual(
                report["violations"][0]["increased"],
                {"pr_self_hosted_without_owner_same_repo_guard": 1},
            )

    def test_workflow_is_hosted_exact_head_and_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)
        self.assertIn("--base-sha \"$BASE_SHA\"", text)
        self.assertIn("--head-sha \"$HEAD_SHA\"", text)
        self.assertNotIn("self-hosted", text.split("jobs:", 1)[1].split("run:", 1)[0])
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl ", text)


if __name__ == "__main__":
    unittest.main()
