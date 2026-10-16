"""Adversarial, local-only tests for matrix capacity source ratchet (#633)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_selfhosted_matrix_capacity_ratchet_20261016 import (
    InvalidSource, _matrix_count, findings, inspect, main,
)

ROOT = ".github/workflows/fixture.yml"


def workflow(strategy, runner="[self-hosted, zcloud, vps]"):
    return f"""name: Offline fixture
on: [workflow_dispatch]
jobs:
  probe:
    runs-on: {runner}
    strategy:
{strategy}
    steps:
      - run: echo safe
"""


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()


class MatrixContract(unittest.TestCase):
    def test_matrix_cap_bounded_and_serial(self):
        src = workflow("      max-parallel: 1\n      matrix:\n        shard: [a, b]")
        self.assertEqual(findings(src), [])

    def test_missing_parallel_limit_denied(self):
        self.assertIn("unsafe_max_parallel:None", findings(
            workflow("      matrix:\n        shard: [a, b]"))[0][1])

    def test_parallel_two_denied(self):
        self.assertTrue(any("unsafe_max_parallel:2" in item[1] for item in findings(
            workflow("      max-parallel: 2\n      matrix:\n        shard: [a, b]"))))

    def test_boolean_parallel_denied(self):
        self.assertTrue(findings(workflow(
            "      max-parallel: true\n      matrix:\n        shard: [a, b]")))

    def test_dynamic_matrix_denied(self):
        self.assertIn(("probe", "unknown_matrix_size"), findings(
            workflow("      max-parallel: 1\n      matrix: ${{ fromJSON(inputs.matrix) }}")))

    def test_product_over_four_denied(self):
        risks = findings(workflow(
            "      max-parallel: 1\n      matrix:\n        a: [1, 2, 3]\n        b: [x, y]"))
        self.assertIn(("probe", "matrix_fanout:6"), risks)

    def test_include_cannot_hide_extra_fanout(self):
        risks = findings(workflow(
            "      max-parallel: 1\n      matrix:\n        a: [1, 2, 3, 4]\n"
            "        include:\n          - {a: 5}"))
        self.assertIn(("probe", "matrix_fanout:5"), risks)

    def test_hosted_matrix_out_of_scope(self):
        self.assertEqual(findings(workflow(
            "      matrix:\n        shard: [1, 2, 3, 4, 5]",
            runner="ubuntu-latest")), [])

    def test_dynamic_runner_needs_review(self):
        self.assertTrue(findings(workflow(
            "      matrix:\n        shard: [1, 2]",
            runner="${{ matrix.runner }}")))

    def test_reusable_workflow_caller_may_fan_out_to_vps(self):
        src = """name: Reusable caller
on: [workflow_dispatch]
jobs:
  delegated:
    uses: ./.github/workflows/worker-vps.yml
    strategy:
      matrix:
        shard: [1, 2, 3, 4, 5]
"""
        risks = findings(src)
        self.assertIn(("delegated", "unsafe_max_parallel:None"), risks)
        self.assertIn(("delegated", "matrix_fanout:5"), risks)

    def test_reusable_caller_with_bounded_serial_matrix(self):
        src = """name: Safe bounded delegation
on: [workflow_dispatch]
jobs:
  delegated:
    uses: ./.github/workflows/reusable.yml
    strategy:
      max-parallel: 1
      matrix:
        shard: [1, 2]
"""
        self.assertEqual(findings(src), [])

    def test_duplicate_yaml_key_fail_closed(self):
        with self.assertRaises(InvalidSource):
            findings(workflow("      max-parallel: 1\n      max-parallel: 2\n"
                              "      matrix:\n        shard: [1, 2]"))

    def test_yaml_alias_unknown_rejected(self):
        with self.assertRaises(InvalidSource):
            findings(workflow("      max-parallel: 1\n      matrix: *missing"))

    def test_bounded_zero_axis_include(self):
        self.assertEqual(_matrix_count({"include": [{"a": 1}, {"a": 2}]}), 2)


class GitTreeContract(unittest.TestCase):
    def setUp(self):
        self.original = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        os.chdir(self.temp.name)
        git("init", "-q")
        git("config", "user.name", "zCloud offline test")
        git("config", "user.email", "offline@example.invalid")
        Path(ROOT).parent.mkdir(parents=True, exist_ok=True)
        self.good = workflow("      max-parallel: 1\n"
                             "      matrix:\n        shard: [a, b]")
        Path(ROOT).write_text(self.good)
        git("add", ".")
        git("commit", "-qm", "base")
        self.base = git("rev-parse", "HEAD")

    def tearDown(self):
        os.chdir(self.original)
        self.temp.cleanup()

    def commit(self, content):
        Path(ROOT).write_text(content)
        git("add", ".")
        git("commit", "-qm", "candidate")
        return git("rev-parse", "HEAD")

    def test_exact_git_commit_accepts_serial_matrix(self):
        head = self.commit(workflow("      max-parallel: 1\n"
                                    "      matrix:\n        shard: [a, b, c]"))
        outcome = inspect(self.base, head)
        self.assertEqual(outcome["new_risks"], [])
        self.assertFalse(outcome["production_authorized"])

    def test_exact_git_commit_rejects_new_fanout(self):
        head = self.commit(workflow("      max-parallel: 5\n"
                                    "      matrix:\n        shard: [a, b, c, d, e]"))
        result = inspect(self.base, head)
        self.assertEqual(len(result["new_risks"]), 2)
        self.assertEqual(main(["--base", self.base, "--head", head]), 1)

    def test_legacy_risk_is_not_claimed_as_healthy(self):
        # A change that introduces a *new* finding into an existing bad
        # workflow must still be denied; identical risk is baseline debt.
        head = self.commit(workflow("      max-parallel: 1\n"
                                    "      matrix:\n        shard: [a, b, c, d, e]"))
        self.assertEqual(inspect(self.base, head)["new_risks"][0]["reason"],
                         "matrix_fanout:5")

    def test_modified_legacy_unbounded_matrix_cannot_hide_as_same_risk(self):
        old = workflow("      max-parallel: 1\\n"
                       "      matrix: ${{ fromJSON(inputs.small) }}")
        baseline = self.commit(old)
        latest = self.commit(workflow("      max-parallel: 1\\n"
                                      "      matrix: ${{ fromJSON(inputs.huge) }}"))
        risks = inspect(baseline, latest)["new_risks"]
        self.assertIn("legacy_matrix_configuration_changed",
                      [risk["reason"] for risk in risks])

    def test_step_only_change_preserves_existing_baseline_risk(self):
        old = workflow("      max-parallel: 1\\n"
                       "      matrix: ${{ fromJSON(inputs.risky) }}")
        baseline = self.commit(old)
        latest = self.commit(old.replace("echo safe", "echo changed"))
        self.assertEqual(inspect(baseline, latest)["new_risks"], [])

    def test_refuses_noncommit_git_object(self):
        blob = git("rev-parse", f"{self.base}:" + ROOT)
        with self.assertRaises(InvalidSource):
            inspect(self.base, blob)

    def test_new_symlink_workflow_is_not_accepted(self):
        Path(ROOT).unlink()
        os.symlink("missing.yml", ROOT)
        git("add", ".")
        git("commit", "-qm", "symlink")
        with self.assertRaises(InvalidSource):
            inspect(self.base, git("rev-parse", "HEAD"))

    def test_invalid_sha_fails_closed(self):
        with self.assertRaises(InvalidSource):
            inspect(self.base, "HEAD")


if __name__ == "__main__":
    unittest.main()
