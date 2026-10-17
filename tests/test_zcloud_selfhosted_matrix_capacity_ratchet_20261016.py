"""Adversarial, local-only tests for matrix capacity source ratchet (#633)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_selfhosted_matrix_capacity_ratchet_20261016 import (
    InvalidSource, _git, _matrix_count, findings, inspect, main,
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


def workflow_with_matrix_groups(count):
    """Independent max-parallel:1 jobs can still run simultaneously."""
    header = "name: Matrix aggregate fixture\non: [workflow_dispatch]\njobs:\n"
    job = (
        "  probe{index}:\n"
        "    runs-on: [self-hosted, zcloud, vps]\n"
        "    strategy:\n"
        "      max-parallel: 1\n"
        "      matrix:\n"
        "        shard: [a, b]\n"
        "    steps:\n"
        "      - run: echo safe\n"
    )
    return header + "".join(job.format(index=i) for i in range(count))


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

    def test_dynamic_expression_inside_literal_matrix_axis_is_unknown(self):
        risks = findings(workflow(
            "      max-parallel: 1\n"
            "      matrix:\n"
            '        shard: [first, "${{ fromJSON(inputs.dynamic) }}"]'))
        self.assertIn(("probe", "unknown_matrix_size"), risks)

    def test_dynamic_matrix_include_row_is_unknown(self):
        risks = findings(workflow(
            "      max-parallel: 1\n"
            "      matrix:\n"
            "        shard: [first]\n"
            '        include: [{shard: "${{ fromJSON(inputs.dynamic) }}"}]'))
        self.assertIn(("probe", "unknown_matrix_size"), risks)

    def test_yaml_nonfinite_matrix_values_are_denied(self):
        self.assertIn(("probe", "unknown_matrix_size"), findings(
            workflow("      max-parallel: 1\n"
                     "      matrix:\n"
                     "        shard: [.nan, ok]")))
        self.assertEqual(findings(workflow(
            "      max-parallel: 1\n"
            "      matrix:\n"
            "        shard: [1, 2.5]")), [])

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

    def test_bare_custom_runner_label_is_vps_capable(self):
        for label in ("vps", "zcloud", "my-private-runner",
                      "ubuntu-latest-evil", "ubuntu-24x04", "self-hosted"):
            with self.subTest(label=label):
                risks = findings(workflow(
                    "      matrix:\n        shard: [one, two]", runner=label))
                self.assertIn(("probe", "unsafe_max_parallel:None"), risks)

    def test_canonical_hosted_labels_stay_out_of_vps_policy(self):
        for label in ("ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04",
                      "ubuntu-24.04-arm", "windows-latest", "windows-2022",
                      "macos-latest", "macos-15-intel"):
            with self.subTest(label=label):
                self.assertEqual(findings(workflow(
                    "      matrix:\n        shard: [one, two, three, four, five]",
                    runner=label)), [])

    def test_mixed_hosted_and_custom_label_requires_review(self):
        self.assertIn(("probe", "unsafe_max_parallel:None"),
                      findings(workflow(
                          "      matrix:\n        shard: [1, 2]",
                          runner="[ubuntu-latest, custom-vps]")))

    def test_unknown_or_empty_runner_selector_fails_closed(self):
        self.assertIn(("probe", "unsafe_max_parallel:None"),
                      findings(workflow(
                          "      matrix:\n        shard: [1, 2]",
                          runner="null")))

    def test_empty_runner_label_list_is_not_hosted_proof(self):
        for runner in ("[]", "[[]]"):
            with self.subTest(runner=runner):
                self.assertIn(("probe", "unsafe_max_parallel:None"),
                              findings(workflow(
                                  "      matrix:\n        shard: [1, 2]",
                                  runner=runner)))

    def test_numeric_runner_selector_is_not_hosted_proof(self):
        risks = findings(workflow(
            "      matrix:\n        shard: [1, 2]",
            runner="500"))
        self.assertIn(("probe", "unsafe_max_parallel:None"), risks)

    def test_recursive_runner_alias_is_fail_closed(self):
        src = """name: Recursive runner
on: [workflow_dispatch]
jobs:
  probe:
    runs-on: &labels [*labels]
    strategy:
      max-parallel: 1
      matrix:
        shard: [1, 2]
    steps:
      - run: echo safe
"""
        # PyYAML may reject recursion during construction, before the
        # explicit runner selector cycle guard; either DENY is correct.
        with self.assertRaisesRegex(InvalidSource, "unparseable_yaml|cyclic_runner_selection"):
            findings(src)

    def test_oversized_runner_alias_is_fail_closed(self):
        src = workflow(
            "      matrix:\n        shard: [1, 2]",
            runner="[" + ", ".join(["ubuntu-latest"] * 65) + "]")
        with self.assertRaisesRegex(InvalidSource, "oversized_runner_selection"):
            findings(src)

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

    def test_aggregate_matrix_jobs_limit_four(self):
        self.assertEqual(findings(workflow_with_matrix_groups(4)), [])
        self.assertIn(("__workflow__", "aggregate_matrix_groups:5"),
                      findings(workflow_with_matrix_groups(5)))

    def test_aggregate_capacity_no_debt_bypass(self):
        self.assertIn(("__workflow__", "aggregate_matrix_groups:10"),
                      findings(workflow_with_matrix_groups(10)))

    def test_yaml_nested_sequence_is_bounded(self):
        # A syntactically valid deeply nested source must not spend
        # unbounded stack/CPU in the custom YAML parser.
        source = "name: bomb\njobs:\n  probe:\n    runs-on: [self-hosted]\n    strategy:\n      matrix: " + "[" * 70 + "x" + "]" * 70 + "\n"
        with self.assertRaises(InvalidSource):
            findings(source)

    def test_yaml_node_count_is_bounded(self):
        source = "name: bomb\njobs:\n  probe:\n    runs-on: [self-hosted]\n    strategy:\n      matrix: [" + ", ".join(["x"] * 30_001) + "]\n"
        with self.assertRaises(InvalidSource):
            findings(source)

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
        old = workflow("      max-parallel: 1\n"
                       "      matrix: ${{ fromJSON(inputs.small) }}")
        baseline = self.commit(old)
        latest = self.commit(workflow("      max-parallel: 1\n"
                                      "      matrix: ${{ fromJSON(inputs.huge) }}"))
        risks = inspect(baseline, latest)["new_risks"]
        self.assertIn("legacy_matrix_configuration_changed",
                      [risk["reason"] for risk in risks])

    def test_step_only_change_preserves_existing_baseline_risk(self):
        old = workflow("      max-parallel: 1\n"
                       "      matrix: ${{ fromJSON(inputs.risky) }}")
        baseline = self.commit(old)
        latest = self.commit(old.replace("echo safe", "echo changed"))
        self.assertEqual(inspect(baseline, latest)["new_risks"], [])

    def test_git_rejects_new_fifth_individually_bounded_matrix(self):
        base = self.commit(workflow_with_matrix_groups(4))
        head = self.commit(workflow_with_matrix_groups(5))
        risks = inspect(base, head)["new_risks"]
        self.assertIn("aggregate_matrix_groups:5", [risk["reason"] for risk in risks])

    def test_git_rejects_relabelled_legacy_aggregate_matrix(self):
        old = workflow_with_matrix_groups(5)
        base = self.commit(old)
        latest = self.commit(old.replace("probe4:", "renamed_probe4:"))
        risks = inspect(base, latest)["new_risks"]
        self.assertIn("legacy_aggregate_matrix_configuration_changed",
                      [risk["reason"] for risk in risks])

    def test_git_preserves_legacy_aggregate_on_step_only_change(self):
        old = workflow_with_matrix_groups(5)
        base = self.commit(old)
        latest = self.commit(old.replace("echo safe", "echo changed"))
        self.assertEqual(inspect(base, latest)["new_risks"], [])

    def test_git_new_workflow_with_five_serial_matrix_groups_denied(self):
        # A new added workflow has no baseline and cannot hide behind
        # individually bounded per-job strategies.
        Path(ROOT).unlink()
        git("add", "-A")
        git("commit", "-qm", "remove baseline workflow")
        empty = git("rev-parse", "HEAD")
        Path(ROOT).write_text(workflow_with_matrix_groups(5))
        git("add", ".")
        git("commit", "-qm", "add new workflow")
        risks = inspect(empty, git("rev-parse", "HEAD"))["new_risks"]
        self.assertIn("aggregate_matrix_groups:5", [risk["reason"] for risk in risks])

    def test_git_stdout_has_streaming_size_cap(self):
        with self.assertRaisesRegex(InvalidSource, "git_output_too_large"):
            _git("rev-parse", "HEAD", max_output=4)

    def test_git_nonzero_status_denied(self):
        with self.assertRaisesRegex(InvalidSource, "git_source_unavailable"):
            _git("cat-file", "-t", "0" * 40, max_output=200)

    def test_git_revision_denies_custom_label_but_not_hosted(self):
        hosted = workflow(
            "      matrix:\n        shard: [a, b, c, d, e]",
            runner="ubuntu-latest")
        base = self.commit(hosted)
        unsafe = self.commit(workflow(
            "      matrix:\n        shard: [a, b, c, d, e]",
            runner="vps"))
        result = inspect(base, unsafe)
        self.assertEqual(
            {row["reason"] for row in result["new_risks"]},
            {"unsafe_max_parallel:None", "matrix_fanout:5"})
        self.assertFalse(result["mutation_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["production_authorized"])
        self.assertEqual(main(["--base", base, "--head", unsafe]), 1)
        healed = self.commit(hosted)
        self.assertEqual(inspect(unsafe, healed)["new_risks"], [])
        self.assertEqual(main(["--base", unsafe, "--head", healed]), 0)

    def test_uppercase_yaml_extension_does_not_bypass_no_new_debt(self):
        # A changed YAML file whose casing differs must be a DENY, not
        # incorrectly ignored as outside the changed-workflow inventory.
        for suffix in (".YML", ".Yaml", ".YAML"):
            with self.subTest(suffix=suffix):
                path = ".github/workflows/strange" + suffix
                Path(path).write_text(workflow(
                    "      matrix:\n        shard: [a, b, c, d, e]",
                    runner="vps"))
                git("add", ".")
                git("commit", "-qm", "unusual extension")
                head = git("rev-parse", "HEAD")
                with self.assertRaisesRegex(InvalidSource, "noncanonical_workflow_extension"):
                    inspect(self.base, head)

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
