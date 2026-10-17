"""Isolated real-Git regression tests for hosted-only job timeout admission."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_hosted_job_timeout_ratchet_20261016.py"
SPEC = importlib.util.spec_from_file_location("hosted_timeout_policy", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
WORKFLOW = ".github/workflows/hosted-probe.yml"


def content(runner="ubuntu-latest", timeout=8, job="gate", step="echo safe"):
    bound = "" if timeout is None else f"    timeout-minutes: {timeout}\n"
    return (
        "name: Hosted probe\non: [pull_request]\n"
        f"jobs:\n  {job}:\n    runs-on: {runner}\n"
        + bound + "    steps:\n      - run: " + step + "\n"
    )


class HostedTimeoutPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self._restore)
        self.git("init", "-q")
        self.git("config", "user.email", "policy@example.test")
        self.git("config", "user.name", "Policy Test")
        self.base = self.commit(content())

    def _restore(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, text, path=WORKFLOW):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def assert_denied(self, source, reason, path=WORKFLOW):
        head = self.commit(source, path)
        scanned, old, new, denied = policy.audit(self.base, head)
        self.assertEqual(scanned, 1)
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0][-1], reason)
        self.assertEqual(policy.main(["--base", self.base, "--head", head]), 1)

    def test_new_hosted_missing_timeout_is_denied(self):
        self.assert_denied(content(timeout=None), "missing_timeout")

    def test_new_hosted_dynamic_timeout_is_denied(self):
        self.assert_denied(content(timeout='"not-an-integer"'), "dynamic_or_invalid_timeout")

    def test_new_hosted_boolean_timeout_is_denied(self):
        self.assert_denied(content(timeout="true"), "dynamic_or_invalid_timeout")

    def test_new_hosted_float_timeout_is_denied(self):
        self.assert_denied(content(timeout="8.0"), "dynamic_or_invalid_timeout")

    def test_new_hosted_zero_timeout_is_denied(self):
        self.assert_denied(content(timeout=0), "timeout_outside_1_to_30")

    def test_new_hosted_31_minute_timeout_is_denied(self):
        self.assert_denied(content(timeout=31), "timeout_outside_1_to_30")

    def test_new_hosted_extremely_large_timeout_is_denied(self):
        self.assert_denied(content(timeout=999999), "timeout_outside_1_to_30")

    def test_literal_1_and_30_are_allowed(self):
        for bound in (1, 30):
            with self.subTest(bound=bound):
                head = self.commit(content(timeout=bound))
                self.assertEqual(policy.audit(self.base, head)[-1], [])

    def test_missing_timeout_legacy_unchanged_is_grandfathered(self):
        legacy = self.commit(content(timeout=None))
        after = self.commit(content(timeout=None, step="echo updated"))
        self.assertEqual(policy.audit(legacy, after)[-1], [])
        self.assertEqual(policy.main(["--base", legacy, "--head", after]), 0)

    def test_legacy_debt_cannot_hide_under_new_job_identifier(self):
        legacy = self.commit(content(timeout=None))
        after = self.commit(content(timeout=None, job="other"))
        self.assertEqual(policy.audit(legacy, after)[-1][0][-1], "missing_timeout")

    def test_legacy_high_timeout_cannot_escalate_under_same_reason(self):
        legacy = self.commit(content(timeout=60))
        head = self.commit(content(timeout=360))
        self.assertEqual(
            policy.audit(legacy, head)[-1][0][-1],
            "timeout_outside_1_to_30",
        )

    def test_legacy_high_timeout_survives_unrelated_step_edit(self):
        legacy = self.commit(content(timeout=60))
        head = self.commit(content(timeout=60, step="echo changed"))
        self.assertEqual(policy.audit(legacy, head)[-1], [])

    def test_legacy_dynamic_timeout_change_requires_full_remediation(self):
        legacy = self.commit(content(timeout='"${{ inputs.a }}"'))
        head = self.commit(content(timeout='"${{ inputs.b }}"'))
        self.assertEqual(
            policy.audit(legacy, head)[-1][0][-1],
            "dynamic_or_invalid_timeout",
        )

    def test_legacy_debt_cannot_hide_under_runner_change(self):
        legacy = self.commit(content(timeout=None))
        after = self.commit(content(runner="windows-latest", timeout=None))
        self.assertEqual(policy.audit(legacy, after)[-1][0][-1], "missing_timeout")

    def test_custom_and_self_hosted_jobs_outside_policy(self):
        for runner in ("vps", "self-hosted", "custom-runner", '"dynamic-runner"'):
            with self.subTest(runner=runner):
                head = self.commit(content(runner=runner, timeout=None))
                self.assertEqual(policy.audit(self.base, head)[-1], [])

    def test_windows_macos_and_ubuntu_managed_labels_covered(self):
        for runner in ("ubuntu-22.04", "ubuntu-24.04-arm", "ubuntu-slim",
                       "windows-2022", "windows-2025", "macos-latest", "macos-15-intel"):
            with self.subTest(runner=runner):
                head = self.commit(content(runner=runner, timeout=None))
                self.assertEqual(policy.audit(self.base, head)[-1][0][-1], "missing_timeout")

    def test_array_of_managed_labels_covered(self):
        head = self.commit(content(runner="[ubuntu-latest]", timeout=None))
        self.assertEqual(policy.audit(self.base, head)[-1][0][-1], "missing_timeout")

    def test_mixed_runner_label_array_outside_hosted_only_policy(self):
        head = self.commit(content(runner="[ubuntu-latest, custom-vps]", timeout=None))
        self.assertEqual(policy.audit(self.base, head)[-1], [])

    def test_reusable_workflow_call_is_not_a_hosted_job(self):
        src = (
            "name: Caller\non: [pull_request]\njobs:\n  reuse:\n"
            "    uses: ./.github/workflows/reusable.yml\n"
        )
        head = self.commit(src)
        self.assertEqual(policy.audit(self.base, head)[-1], [])

    def test_new_workflow_gets_checked(self):
        head = self.commit(content(timeout=None), ".github/workflows/new.yml")
        denied = policy.audit(self.base, head)[-1]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0][0], ".github/workflows/new.yml")

    def test_deleted_workflow_is_not_new_debt(self):
        Path(WORKFLOW).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "delete")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(policy.audit(self.base, head)[-1], [])

    def test_non_workflow_source_change_is_ignored(self):
        head = self.commit("script-change", "scripts/something.txt")
        self.assertEqual(policy.audit(self.base, head), (0, 0, 0, []))

    def test_duplicate_yaml_keys_denied(self):
        head = self.commit(
            content(timeout=5).replace("    timeout-minutes: 5\n",
                                       "    timeout-minutes: 5\n    timeout-minutes: 20\n")
        )
        with self.assertRaisesRegex(policy.InvalidSource, "duplicate_or_invalid"):
            policy.audit(self.base, head)

    def test_broken_yaml_denied(self):
        head = self.commit("jobs:\n  gate: [\n")
        with self.assertRaises(policy.InvalidSource):
            policy.audit(self.base, head)

    def test_symlink_workflow_denied(self):
        Path(WORKFLOW).unlink()
        Path(WORKFLOW).symlink_to("target.yml")
        self.git("add", "-A")
        self.git("commit", "-qm", "symlink")
        with self.assertRaisesRegex(policy.InvalidSource, "nonregular"):
            policy.audit(self.base, self.git("rev-parse", "HEAD"))

    def test_case_variant_extension_denied(self):
        head = self.commit(content(timeout=None), ".github/workflows/strange.YML")
        with self.assertRaisesRegex(policy.InvalidSource, "noncanonical"):
            policy.audit(self.base, head)

    def test_deep_yaml_denied(self):
        src = "jobs:\n  gate:\n    runs-on: ubuntu-latest\n    timeout-minutes: " \
              + "[" * 70 + "x" + "]" * 70 + "\n"
        head = self.commit(src)
        with self.assertRaises(policy.InvalidSource):
            policy.audit(self.base, head)

    def test_git_output_is_bounded_before_it_is_buffered(self):
        with self.assertRaisesRegex(policy.InvalidSource, "git_output_too_large"):
            policy.git("show", "--format=fuller", self.base, limit=16)

    def test_failed_git_command_is_denied(self):
        with self.assertRaisesRegex(policy.InvalidSource, "git_command_failed"):
            policy.git("cat-file", "-t", "0" * 40)

    def test_changed_workflow_count_is_bounded(self):
        root = Path(".github/workflows")
        for number in range(513):
            (root / f"too-many-{number:04d}.yml").write_text(
                content(timeout=8), encoding="utf-8"
            )
        self.git("add", "-A")
        self.git("commit", "-qm", "oversized changed workflow inventory")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(
            policy.InvalidSource, "changed_workflow_path_budget_exceeded"
        ):
            policy.audit(self.base, head)

    def test_requires_immutable_40_char_shas(self):
        with self.assertRaisesRegex(policy.InvalidSource, "immutable"):
            policy.audit("main", self.base)
        self.assertEqual(policy.main(["--base", "main", "--head", self.base]), 2)

    def test_baseline_includes_only_changed_files(self):
        ignored = self.commit(content(timeout=None), ".github/workflows/legacy.yml")
        current = self.commit(content(timeout=12, step="echo changed"))
        self.assertEqual(policy.audit(ignored, current)[-1], [])

    def test_changes_from_unbounded_to_bounded_are_allowed(self):
        legacy = self.commit(content(timeout=999))
        head = self.commit(content(timeout=12))
        self.assertEqual(policy.audit(legacy, head)[-1], [])
        self.assertEqual(policy.main(["--base", legacy, "--head", head]), 0)


if __name__ == "__main__":
    unittest.main()
