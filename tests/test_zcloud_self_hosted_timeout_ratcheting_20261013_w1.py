"""Independent deploy-ops ratchet regressions (no overlap with timeout-workflow owner)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_self_hosted_timeout_audit as audit


class TimeoutDebtRatchetingTests(unittest.TestCase):
    def test_dynamic_runner_and_yaml_alias_are_never_silent(self):
        source = """jobs:
  dynamic:
    runs-on: ${{ matrix.runner }}
    timeout-minutes: 5
  alias:
    runs-on: *private_runner
    timeout-minutes: 10
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 50
  misleading:
    runs-on: not-self-hosted
    timeout-minutes: 99
"""
        rows = audit.inspect_workflow(".github/workflows/dynamic.yml", source, max_minutes=30)
        self.assertEqual(["dynamic", "alias", "misleading"], [item.job for item in rows])
        self.assertEqual(["dynamic_runner_unknown", "dynamic_runner_unknown", "runner_target_unknown"], [item.status for item in rows])
        counts = audit.summarize(rows, max_minutes=30)
        self.assertEqual(3, counts["finding_count"])
        self.assertEqual(2, counts["by_status"]["dynamic_runner_unknown"])
        self.assertEqual(1, counts["by_status"]["runner_target_unknown"])
        self.assertFalse(counts["mutation_performed"])

    def test_valid_alternate_yaml_indentation_cannot_hide_private_runner(self):
        source = """jobs:
    deeper:
        runs-on: [self-hosted, zcloud, vps]
        timeout-minutes: 75
    custom:
        runs-on: [zcloud, vps]
        timeout-minutes: 5
"""
        rows = audit.inspect_workflow(".github/workflows/deeper.yml", source, max_minutes=30)
        self.assertEqual(["deeper", "custom"], [row.job for row in rows])
        self.assertEqual(["over_limit", "runner_target_unknown"], [row.status for row in rows])

    def test_alternate_child_key_indentation_does_not_match_nested_script(self):
        source = """jobs:
  proof:
      runs-on: ubuntu-latest
      steps:
        - run: |
            echo runs-on: self-hosted
            echo timeout-minutes: 999
  private:
      name: private runner proof
      runs-on: my-special-runner
      timeout-minutes: 45
"""
        rows = audit.inspect_workflow(".github/workflows/alternate.yml", source, max_minutes=30)
        self.assertEqual(["private"], [row.job for row in rows])
        self.assertEqual(["runner_target_unknown"], [row.status for row in rows])

    def test_custom_label_and_group_runner_are_never_assumed_hosted(self):
        source = """jobs:
  custom:
    runs-on: [zcloud, vps]
    timeout-minutes: 120
  group:
    runs-on:
      group: private-vps-group
      labels: [zcloud, vps]
    timeout-minutes: 5
  single_custom:
    runs-on: private-vps-label
    timeout-minutes: 5
  official:
    runs-on: ubuntu-24.04-arm
    timeout-minutes: 50
  official_list:
    runs-on: [windows-2022]
    timeout-minutes: 50
"""
        rows = audit.inspect_workflow(".github/workflows/labels.yml", source, max_minutes=30)
        self.assertEqual(["custom", "group", "single_custom"], [row.job for row in rows])
        self.assertTrue(all(row.status == "runner_target_unknown" for row in rows))
        self.assertEqual(3, audit.summarize(rows, max_minutes=30)["by_status"]["runner_target_unknown"])

    def test_custom_label_timeout_debt_is_rejected_by_strict_new_debt_gate(self):
        path = ".github/workflows/admitted.yml"
        baseline = [audit.TimeoutFinding(path, "old", 10, "bounded")]
        candidate = [
            audit.TimeoutFinding(path, "old", 10, "bounded"),
            audit.TimeoutFinding(path, "new", 3, "runner_target_unknown"),
        ]
        self.assertEqual(candidate[1:], audit.new_debt(baseline, candidate, deny_worsening=True))

    def test_yaml_block_scalar_runner_expressions_are_not_ignored(self):
        source = """jobs:
  folded:
    runs-on: >-
      ${{ matrix.runner }}
    timeout-minutes: 10
  literal:
    runs-on: |
      self-hosted
      zcloud
    timeout-minutes: 15
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 50
"""
        rows = audit.inspect_workflow(".github/workflows/scalars.yml", source, max_minutes=30)
        self.assertEqual(["folded", "literal"], [row.job for row in rows])
        self.assertEqual(["dynamic_runner_unknown", "bounded"], [row.status for row in rows])

    def test_bounded_self_hosted_label_is_still_checked(self):
        source = """jobs:
  real:
    runs-on: [self-hosted, zcloud, vps]
    timeout-minutes: 10
  different:
    runs-on: not-self-hosted
    timeout-minutes: 300
"""
        rows = audit.inspect_workflow(".github/workflows/labels.yml", source, max_minutes=30)
        self.assertEqual([("real", "bounded"), ("different", "runner_target_unknown")], [(r.job, r.status) for r in rows])

    def test_legacy_excessive_timeout_cannot_get_longer_or_disappear(self):
        path = ".github/workflows/old.yml"
        base = [audit.TimeoutFinding(path, "slow", 35, "over_limit")]
        worsened = [audit.TimeoutFinding(path, "slow", 60, "over_limit")]
        missing = [audit.TimeoutFinding(path, "slow", None, "missing")]
        improved = [audit.TimeoutFinding(path, "slow", 31, "over_limit")]
        fixed = [audit.TimeoutFinding(path, "slow", 30, "bounded")]
        self.assertEqual(worsened, audit.new_debt(base, worsened, deny_worsening=True))
        self.assertEqual(missing, audit.new_debt(base, missing, deny_worsening=True))
        self.assertEqual([], audit.new_debt(base, improved, deny_worsening=True))
        self.assertEqual([], audit.new_debt(base, fixed, deny_worsening=True))

    def test_new_dynamic_runner_creates_policy_debt(self):
        row = audit.TimeoutFinding(".github/workflows/new.yml", "probe", 5, "dynamic_runner_unknown")
        self.assertEqual([row], audit.new_debt([], [row]))

    def test_strict_cli_rejects_worsened_legacy_timeout_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for key, value in (("user.email", "audit@example.invalid"), ("user.name", "Audit Test")):
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            wf = root / ".github" / "workflows" / "old.yml"
            wf.write_text(
                "jobs:\n  legacy:\n    runs-on: self-hosted\n    timeout-minutes: 35\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            wf.write_text(
                "jobs:\n  legacy:\n    runs-on: self-hosted\n    timeout-minutes: 60\n",
                encoding="utf-8",
            )
            before = wf.read_bytes()
            args = [
                sys.executable, str(Path(audit.__file__).resolve()),
                "--repo-root", str(root), "--base-ref", sha,
            ]
            ordinary = subprocess.run(args, capture_output=True, text=True, check=False)
            strict = subprocess.run(args + ["--require-no-new-debt"], capture_output=True, text=True, check=False)
            self.assertEqual(0, ordinary.returncode, ordinary.stderr)
            self.assertEqual(0, json.loads(ordinary.stdout)["new_debt_count"])
            self.assertEqual(1, strict.returncode, strict.stderr)
            self.assertEqual(1, json.loads(strict.stdout)["new_debt_count"])
            self.assertEqual(before, wf.read_bytes())

    def test_existing_missing_timeout_can_be_fixed(self):
        base = [audit.TimeoutFinding("x.yml", "x", None, "missing")]
        head = [audit.TimeoutFinding("x.yml", "x", 10, "bounded")]
        self.assertEqual([], audit.new_debt(base, head))


if __name__ == "__main__":
    unittest.main()
