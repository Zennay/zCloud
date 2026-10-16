"""Real Git and adversarial tests for hosted matrix fan-out no-new-debt."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_hosted_matrix_fanout_ratchet_20261016.py"
SPEC = importlib.util.spec_from_file_location("hosted_matrix_policy", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
WORKFLOW = ".github/workflows/hosted-matrix-probe.yml"


def fixture(runner="ubuntu-latest", matrix="        shard: [a, b]",
            parallel="      max-parallel: 2\n", step="echo safe", job="test"):
    return (
        "name: Hosted matrix proof\non: [pull_request]\njobs:\n"
        f"  {job}:\n    runs-on: {runner}\n    strategy:\n"
        + parallel + "      matrix:\n" + matrix + "\n"
        + "    steps:\n      - run: " + step + "\n"
    )


def reasons(source):
    return {finding[-1] for finding in policy.violations(source)}


class HostedMatrixPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self._restore)
        self.git("init", "-q")
        self.git("config", "user.email", "policy@example.test")
        self.git("config", "user.name", "Policy Test")
        self.base = self.commit(fixture())

    def _restore(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, source, path=WORKFLOW):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-qm", "matrix-fixture")
        return self.git("rev-parse", "HEAD")

    def test_bounded_hosted_matrix_accepted(self):
        self.assertEqual(reasons(fixture()), set())

    def test_matrix_16_boundary_with_max_parallel_4(self):
        src = fixture(matrix="        shard: [" + ", ".join(map(str, range(16))) + "]",
                      parallel="      max-parallel: 4\n")
        self.assertEqual(reasons(src), set())

    def test_five_parallel_denied(self):
        self.assertIn("max_parallel_outside_1_to_4",
                      reasons(fixture(parallel="      max-parallel: 5\n")))

    def test_missing_parallel_denied(self):
        self.assertIn("max_parallel_not_static_integer",
                      reasons(fixture(parallel="")))

    def test_dynamic_parallel_denied(self):
        expr = "$" + "{{ fromJSON(inputs.parallel) }}"
        self.assertIn("max_parallel_not_static_integer",
                      reasons(fixture(parallel=f"      max-parallel: '{expr}'\n")))

    def test_boolean_parallel_denied(self):
        self.assertIn("max_parallel_not_static_integer",
                      reasons(fixture(parallel="      max-parallel: true\n")))

    def test_zero_parallel_denied(self):
        self.assertIn("max_parallel_outside_1_to_4",
                      reasons(fixture(parallel="      max-parallel: 0\n")))

    def test_negative_parallel_denied(self):
        self.assertIn("max_parallel_outside_1_to_4",
                      reasons(fixture(parallel="      max-parallel: -1\n")))

    def test_more_than_16_matrix_entries_denied(self):
        self.assertIn("matrix_fanout_over_16", reasons(fixture(
            matrix="        shard: [" + ", ".join(map(str, range(17))) + "]"
        )))

    def test_multiple_axes_product_denied(self):
        self.assertIn("matrix_fanout_over_16", reasons(fixture(
            matrix="        a: [a, b, c, d, e]\n        b: [1, 2, 3, 4]"
        )))

    def test_include_only_safe_four_entries(self):
        self.assertEqual(reasons(fixture(
            matrix="        include:\n"
                   + "\n".join(f"          - os: os{i}" for i in range(4))
        )), set())

    def test_include_only_over_16_denied(self):
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix="        include:\n"
                   + "\n".join(f"          - os: os{i}" for i in range(17))
        )))

    def test_include_additions_conservative(self):
        self.assertIn("matrix_fanout_over_16", reasons(fixture(
            matrix="        shard: [" + ", ".join(map(str, range(15))) + "]\n"
                   "        include:\n          - shard: 16\n          - shard: 17"
        )))

    def test_exclude_does_not_subtract_unknown_slots(self):
        self.assertIn("matrix_fanout_over_16", reasons(fixture(
            matrix="        shard: [" + ", ".join(map(str, range(17))) + "]\n"
                   "        exclude:\n          - shard: 0"
        )))

    def test_dynamic_axis_expression_denied(self):
        expr = "$" + "{{ fromJSON(inputs.platforms) }}"
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix=f"        platform: '{expr}'"
        )))

    def test_expression_inside_axis_denied(self):
        expr = "$" + "{{ github.ref }}"
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix=f"        shard: [a, '{expr}']"
        )))

    def test_empty_axis_denied(self):
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix="        shard: []"
        )))

    def test_nested_axis_denied(self):
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix="        shard: [[a, b], [c, d]]"
        )))

    def test_nonfinite_axis_denied(self):
        self.assertIn("dynamic_or_invalid_matrix", reasons(fixture(
            matrix="        shard: [.nan, b]"
        )))

    def test_hosted_runners_only(self):
        for runner in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-slim",
                       "windows-2022", "windows-latest", "macos-latest",
                       "macos-15-intel", "[ubuntu-latest]"):
            with self.subTest(runner=runner):
                self.assertIn("max_parallel_not_static_integer",
                              reasons(fixture(runner=runner, parallel="")))

    def test_custom_self_hosted_runner_outside_policy(self):
        for runner in ("self-hosted", "vps", "custom", "[ubuntu-latest, self-hosted]"):
            with self.subTest(runner=runner):
                self.assertEqual(reasons(fixture(runner=runner, parallel="")), set())

    def test_reusable_workflow_caller_outside_policy(self):
        doc = "name: Caller\non: [pull_request]\njobs:\n  call:\n    uses: ./.github/workflows/vps.yml\n"
        self.assertEqual(reasons(doc), set())

    def test_aggregate_two_ten_shard_matrices_denied(self):
        one = fixture(matrix="        shard: [" + ", ".join(map(str, range(10))) + "]")
        two = one.replace("  test:\n", "  other:\n")
        combined = one + two.split("jobs:\n", 1)[1]
        self.assertIn("aggregate_hosted_matrix_over_16", reasons(combined))

    def test_aggregate_two_eight_shard_matrices_allowed(self):
        one = fixture(matrix="        shard: [" + ", ".join(map(str, range(8))) + "]")
        two = one.replace("  test:\n", "  other:\n")
        combined = one + two.split("jobs:\n", 1)[1]
        self.assertNotIn("aggregate_hosted_matrix_over_16", reasons(combined))

    def test_new_bad_matrix_denied_real_git(self):
        after = self.commit(fixture(parallel=""))
        examined, _, _, risks = policy.audit(self.base, after)
        self.assertEqual(examined, 1)
        self.assertIn("max_parallel_not_static_integer", {r[-1] for r in risks})
        self.assertEqual(policy.main(["--base", self.base, "--head", after]), 1)

    def test_legacy_bad_matrix_step_only_edit_grandfathered(self):
        legacy = self.commit(fixture(parallel=""))
        updated = self.commit(fixture(parallel="", step="echo changed"))
        self.assertEqual(policy.audit(legacy, updated)[-1], [])
        self.assertEqual(policy.main(["--base", legacy, "--head", updated]), 0)

    def test_same_risk_category_growth_does_not_escape(self):
        legacy = self.commit(fixture(matrix="        shard: [" + ", ".join(map(str, range(17))) + "]"))
        increased = self.commit(fixture(matrix="        shard: [" + ", ".join(map(str, range(20))) + "]"))
        self.assertIn("matrix_fanout_over_16",
                      {r[-1] for r in policy.audit(legacy, increased)[-1]})

    def test_bounded_remediation_accepted(self):
        bad = self.commit(fixture(parallel=""))
        fixed = self.commit(fixture(parallel="      max-parallel: 2\n"))
        self.assertEqual(policy.audit(bad, fixed)[-1], [])

    def test_new_bad_workflow_detected(self):
        after = self.commit(fixture(parallel=""), ".github/workflows/new.yml")
        self.assertEqual(policy.audit(self.base, after)[-1][0][0],
                         ".github/workflows/new.yml")

    def test_symlink_workflow_denied(self):
        Path(WORKFLOW).unlink()
        Path(WORKFLOW).symlink_to("elsewhere.yml")
        self.git("add", "-A")
        self.git("commit", "-qm", "symlink")
        with self.assertRaisesRegex(policy.InvalidSource, "nonregular"):
            policy.audit(self.base, self.git("rev-parse", "HEAD"))

    def test_duplicate_keys_denied(self):
        after = self.commit(fixture().replace("      max-parallel: 2\n",
                   "      max-parallel: 2\n      max-parallel: 4\n"))
        with self.assertRaises(policy.InvalidSource):
            policy.audit(self.base, after)

    def test_yaml_complexity_denied(self):
        source = fixture().replace("        shard: [a, b]",
                                   "        shard: " + "[" * 70 + "x" + "]" * 70)
        with self.assertRaises(policy.InvalidSource):
            policy.violations(source)

    def test_noncanonical_yaml_extension_denied(self):
        after = self.commit(fixture(), ".github/workflows/bad.YML")
        with self.assertRaisesRegex(policy.InvalidSource, "noncanonical"):
            policy.audit(self.base, after)

    def test_git_bytes_capped_before_buffering(self):
        with self.assertRaisesRegex(policy.InvalidSource, "git_output_too_large"):
            policy.git("show", "--format=fuller", self.base, limit=20)

    def test_workflow_filename_newline_cannot_forge_log_marker(self):
        bad = ".github/workflows/fake\nZCLOUD_HOSTED_MATRIX=PASS.yml"
        after = self.commit(fixture(parallel=""), bad)
        with self.assertRaisesRegex(policy.InvalidSource, "workflow_path_control_character"):
            policy.audit(self.base, after)

    def test_job_identifier_control_character_rejected(self):
        source = fixture().replace(
            "  test:\n", '  "bad\\njob":\n'
        )
        after = self.commit(source)
        with self.assertRaisesRegex(policy.InvalidSource, "job_identifier_control_character"):
            policy.audit(self.base, after)

    def test_aggregate_legacy_growth_is_new_debt(self):
        def pair(size):
            one = fixture(matrix="        shard: [" + ", ".join(map(str, range(size))) + "]")
            two = one.replace("  test:\n", "  other:\n")
            return one + two.split("jobs:\n", 1)[1]
        legacy = self.commit(pair(9))
        new = self.commit(pair(10))
        self.assertIn("aggregate_hosted_matrix_over_16",
                      {x[-1] for x in policy.audit(legacy, new)[-1]})

    def test_sha_must_be_immutable_commit(self):
        with self.assertRaises(policy.InvalidSource):
            policy.audit("main", self.base)

    def test_irrelevant_source_edit_skips_workflows(self):
        after = self.commit("unrelated", "docs/readme.txt")
        self.assertEqual(policy.audit(self.base, after), (0, 0, 0, []))

    def test_delete_workflow_cannot_add_debt(self):
        Path(WORKFLOW).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "deleted")
        self.assertEqual(policy.audit(self.base, self.git("rev-parse", "HEAD"))[-1], [])


if __name__ == "__main__":
    unittest.main()
