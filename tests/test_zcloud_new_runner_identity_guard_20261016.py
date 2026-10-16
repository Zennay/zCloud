"""Adversarial source-only admission tests for #633 runner identity regression."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import subprocess
import tempfile

file = Path(__file__).resolve().parents[1] / "scripts/zcloud_new_runner_identity_guard_20261016.py"
spec = importlib.util.spec_from_file_location("guard", file)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
PIN = "d23441a48e516b6c34aea4fa41551a30e30af803"


def wf(runner="[self-hosted, zcloud, vps]", guard=True):
    cmd = "python3 scripts/zcloud_vps_runner_guard.py --json" if guard else "echo untrusted"
    return f'''name: guarded
on:
  workflow_dispatch:
jobs:
  audit:
    runs-on: {runner}
    steps:
      - uses: actions/checkout@{PIN}
        with:
          persist-credentials: false
      - name: Identity first
        run: |
          set -euo pipefail
          {cmd}
      - run: echo after
'''


class IdentityPolicyTests(unittest.TestCase):
    def test_guarded_self_hosted_allowed(self):
        self.assertEqual(g.unsafe_jobs(wf()), {})

    def test_new_unguarded_job_denied(self):
        self.assertEqual(g.unsafe_jobs(wf(guard=False)), {"audit": "missing_early_runner_guard"})

    def test_hosted_allowed(self):
        self.assertEqual(g.unsafe_jobs(wf("ubuntu-latest", False)), {})

    def test_ftmo_dedicated_runner_outside_scope(self):
        self.assertEqual(g.unsafe_jobs(wf("[self-hosted, ftmo-research]", False)), {})

    def test_dynamic_runner_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf("$" + "{{ inputs.runner }}", False)))

    def test_untrusted_action_before_identity_denied(self):
        text = wf().replace("      - name: Identity first",
                            "      - uses: malicious/action@main\n      - name: Identity first")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_fake_quoted_guard_denied(self):
        text = wf().replace("python3 scripts/zcloud_vps_runner_guard.py --json",
                            "echo 'python3 scripts/zcloud_vps_runner_guard.py --json'")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_guard_in_second_script_line_denied(self):
        text = wf().replace("          python3 scripts/",
                            "          echo bad\n          python3 scripts/")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_skipped_guard_step_denied(self):
        text = wf().replace("        run: |", "        if: false\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_guard_continue_on_error_denied(self):
        text = wf().replace("        run: |", "        continue-on-error: true\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_step_conditional_guard_denied(self):
        text = wf().replace("        run: |", "        if: github.event_name == 'workflow_dispatch'\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_failed_open_guard_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace("--json", "--json || true")))

    def test_custom_policy_override_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace("--json", "--policy /tmp/forged.json")))

    def test_folded_guard_script_is_not_trustworthy(self):
        for scalar in (">", ">-"):
            with self.subTest(scalar=scalar):
                self.assertIn("audit", g.unsafe_jobs(wf().replace("run: |", "run: " + scalar)))

    def test_custom_step_shell_cannot_hide_runner_guard(self):
        for shell in ("python", "pwsh", "sh", "bash -c 'true' {0}"):
            with self.subTest(shell=shell):
                value = wf().replace("        run: |", "        shell: " + shell + "\n        run: |")
                self.assertIn("audit", g.unsafe_jobs(value))

    def test_explicit_bash_shell_preserves_safe_guard(self):
        value = wf().replace("        run: |", "        shell: bash\n        run: |")
        self.assertEqual(g.unsafe_jobs(value), {})

    def test_job_shell_defaults_cannot_override_guard(self):
        value = wf().replace("    steps:", "    defaults:\n      run:\n        shell: python\n    steps:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_workflow_shell_defaults_cannot_override_guard(self):
        value = wf().replace("jobs:", "defaults:\n  run:\n    shell: python\njobs:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_must_have_literal_steps_mapping(self):
        value = wf().replace("    steps:", "    renamed_steps:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_skipped_checkout_is_not_trusted(self):
        value = wf().replace("        with:", "        if: false\n        with:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_continue_on_error_checkout_is_not_trusted(self):
        value = wf().replace("        with:", "        continue-on-error: true\n        with:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_unpinned_checkout_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace(PIN, "main")))

    def test_no_checkout_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace(
            f"      - uses: actions/checkout@{PIN}\n", "")))

    def test_duplicate_runner_denied(self):
        text = wf().replace("    runs-on:", "    runs-on: ubuntu-latest\n    runs-on:")
        self.assertEqual(g.unsafe_jobs(text), {"audit": "ambiguous_runner"})

    def test_rename_unsafe_job_is_new_debt(self):
        a = g.unsafe_jobs(wf(guard=False))
        b = g.unsafe_jobs(wf(guard=False).replace("  audit:", "  replacement:"))
        self.assertTrue(set(b) - set(a))

    def test_prior_unguarded_job_grandfathered(self):
        old = g.unsafe_jobs(wf(guard=False))
        new = g.unsafe_jobs(wf(guard=False).replace("name: guarded", "name: renamed"))
        self.assertEqual(set(new) - set(old), set())

    def test_inline_job_mapping_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "  injected: {runs-on: [self-hosted, zcloud], steps: []}\n")

    def test_alias_job_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "  injected: *unguarded\n")

    def test_quoted_root_shadow_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + '"jobs": {unsafe: {runs-on: self-hosted}}\n')

    def test_root_yaml_merge_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "<<: *override\n")

    def test_ambiguous_jobs_root_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "\njobs:\n  evil:\n    runs-on: self-hosted\n")

    def test_reusable_job_no_runner_ok(self):
        self.assertEqual(g.unsafe_jobs("jobs:\n  reuse:\n    uses: org/repo/.github/workflows/x.yml@" + PIN), {})

    def test_missing_runner_denied(self):
        self.assertEqual(g.unsafe_jobs(wf().replace(
            "    runs-on: [self-hosted, zcloud, vps]\n", "")), {"audit": "unknown_runner"})

    def test_source_path_rejection(self):
        with self.assertRaises(ValueError):
            g.source("a" * 40, ".github/workflows/../secret.yml")

    def test_bad_sha_rejection(self):
        with self.assertRaises(ValueError):
            g.inventory("main", "a" * 40)

    def test_fail_closed_on_incomplete_git_inventory(self):
        with mock.patch.object(g, "inventory", side_effect=ValueError("truncated")):
            with self.assertRaises(ValueError):
                g.evaluate("a" * 40, "b" * 40)

    def test_changed_only_regression(self):
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/new.yml"]), \
             mock.patch.object(g, "source", side_effect=[None, wf(guard=False)]):
            result = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["mutation_performed"])



class RealGitEvidenceTests(unittest.TestCase):
    def fixture(self, original=None):
        """Build a separate local Git repository without network or system state."""
        from contextlib import contextmanager
        @contextmanager
        def repo():
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["git", "init", "-q", tmp], check=True)
                for key, value in (("user.name", "Offline Test"), ("user.email", "test@example.invalid")):
                    subprocess.run(["git", "-C", tmp, "config", key, value], check=True)
                folder = Path(tmp) / ".github" / "workflows"
                folder.mkdir(parents=True)
                file = folder / "vps.yml"
                file.write_text(original or wf("ubuntu-latest", False))
                self.commit(tmp)
                base = self.rev(tmp)
                yield tmp, file, base
        return repo()

    @staticmethod
    def commit(tmp):
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", tmp, "commit", "-qm", "offline fixture"], check=True)

    @staticmethod
    def rev(tmp):
        return subprocess.check_output(["git", "-C", tmp, "rev-parse", "HEAD"], text=True).strip()

    def active_git(self, tmp):
        actual = subprocess.check_output
        return mock.patch.object(g, "git", side_effect=lambda *args: actual(
            ["git", *args], cwd=tmp, timeout=20, stderr=subprocess.DEVNULL))

    def active_merge(self, tmp):
        actual = subprocess.call
        return mock.patch.object(g.subprocess, "call", side_effect=lambda argv, **kwargs: actual(
            argv, cwd=tmp, **kwargs))

    def test_real_git_added_unguarded_runner_denied(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf(guard=False))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(len(report["new_findings"]), 1)
            self.assertFalse(report["release_authorized"])

    def test_real_git_new_guarded_runner_allowed(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf())
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"], [])
            self.assertFalse(report["mutation_performed"])

    def test_real_git_symlink_workflow_rejected(self):
        with self.fixture() as (tmp, file, base):
            file.unlink()
            file.symlink_to("/tmp/unsafe-source.yml")
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                with self.assertRaises(ValueError):
                    g.evaluate(base, self.rev(tmp))

    def test_real_git_blob_not_commit_rejected(self):
        with self.fixture() as (tmp, file, base):
            blob = subprocess.check_output(["git", "-C", tmp, "hash-object", str(file)], text=True).strip()
            with self.active_git(tmp), self.active_merge(tmp):
                with self.assertRaises(ValueError):
                    g.inventory(blob, base)


if __name__ == "__main__":
    unittest.main()
