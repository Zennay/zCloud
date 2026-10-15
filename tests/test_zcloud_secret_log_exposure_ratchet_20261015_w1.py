"""Adversarial tests for read-only workflow secret log exposure policy."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import zcloud_secret_log_exposure_ratchet_20261015_w1 as gate


class Signatures(unittest.TestCase):
    def hits(self, source, kind):
        return sum(n for (label, _), n in gate.findings(source).items()
                   if label == kind)

    def test_xtrace_short(self):
        self.assertEqual(self.hits("run: set -x", "shell_xtrace"), 1)

    def test_xtrace_combined(self):
        self.assertEqual(self.hits("run: set -euxo pipefail", "shell_xtrace"), 1)

    def test_xtrace_named(self):
        self.assertEqual(self.hits("run: set -o xtrace", "shell_xtrace"), 1)

    def test_safe_shell_flags(self):
        self.assertEqual(self.hits("run: set -euo pipefail", "shell_xtrace"), 0)

    def test_bash_debug(self):
        self.assertEqual(self.hits("run: bash -x /tmp/script.sh", "shell_debug_execution"), 1)

    def test_printenv(self):
        self.assertEqual(self.hits("run: printenv", "print_all_environment"), 1)

    def test_env_pipe(self):
        self.assertEqual(self.hits("run: env | sort", "print_all_environment"), 1)

    def test_env_variable_command_safe(self):
        self.assertEqual(self.hits("run: env FOO=BAR python app.py", "print_all_environment"), 0)

    def test_powershell_env_dump(self):
        self.assertEqual(self.hits("run: Get-ChildItem Env: | Sort-Object",
                                   "powershell_environment_dump"), 1)

    def test_powershell_env_alias(self):
        self.assertEqual(self.hits("run: gci Env:", "powershell_environment_dump"), 1)

    def test_debug_actions_env(self):
        self.assertEqual(self.hits("ACTIONS_STEP_DEBUG: 'true'", "debug_actions_enabled"), 1)
        self.assertEqual(self.hits("ACTIONS_RUNNER_DEBUG=1", "debug_actions_enabled"), 1)

    def test_safe_debug_off(self):
        self.assertEqual(self.hits("ACTIONS_STEP_DEBUG: false", "debug_actions_enabled"), 0)

    def test_echo_secret_expression(self):
        line = "run: echo " + "$" + "{{ secrets.DEPLOY_KEY }}"
        self.assertEqual(self.hits(line, "print_secrets_expression"), 1)

    def test_secret_env_mapping_safe(self):
        line = "env:\n  DEPLOY_KEY: " + "$" + "{{ secrets.DEPLOY_KEY }}"
        self.assertEqual(self.hits(line, "print_secrets_expression"), 0)

    def test_printf_token(self):
        self.assertEqual(self.hits('run: printf "%s" "$GITHUB_TOKEN"',
                                   "print_secret_environment"), 1)

    def test_braced_token(self):
        line = 'run: echo "$' + '{GITHUB_TOKEN}"'
        self.assertEqual(self.hits(line, "print_secret_environment"), 1)

    def test_powershell_token(self):
        self.assertEqual(self.hits('run: Write-Host "$env:GH_TOKEN"',
                                   "print_secret_environment"), 1)

    def test_safe_token_env_mapping(self):
        self.assertEqual(self.hits("env:\n  GH_TOKEN: " + "$" + "{{ github.token }}",
                                   "print_secret_environment"), 0)

    def test_cat_aws_credentials(self):
        self.assertEqual(self.hits("run: cat ~/.aws/credentials",
                                   "print_credential_file"), 1)

    def test_cat_github_env(self):
        self.assertEqual(self.hits('run: cat "$GITHUB_ENV"', "print_github_env_file"), 1)

    def test_cat_braced_github_env(self):
        line = 'run: cat "$' + '{GITHUB_ENV}"'
        self.assertEqual(self.hits(line, "print_github_env_file"), 1)

    def test_comments_ignored(self):
        self.assertFalse(gate.findings("# run: printenv\n# set -x"))

    def test_safe_unicode(self):
        self.assertFalse(gate.findings("name: 🧪\nrun: echo OK"))

    def test_continuation(self):
        self.assertEqual(self.hits("run: set \\\n -x", "shell_xtrace"), 1)

    def test_source_size_bound(self):
        with self.assertRaises(gate.Unverifiable):
            gate.findings("x" * 100_001)


class RealGit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        wfdir = self.root / ".github" / "workflows"
        wfdir.mkdir(parents=True)
        self.wf = wfdir / "safe.yml"
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI")
        self.wf.write_text("name: safe\non: pull_request\njobs: {}\n")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "test")
        return self.git("rev-parse", "HEAD")

    def check(self):
        return gate.check(self.root, self.base, self.git("rev-parse", "HEAD"))

    def test_new_xtrace_denied(self):
        self.wf.write_text("run: set -x\n")
        self.commit()
        self.assertTrue(any("shell_xtrace" in x for x in self.check()[1]))

    def test_new_secret_echo_denied(self):
        self.wf.write_text("run: echo " + "$" + "{{ secrets.TOKEN }}\n")
        self.commit()
        self.assertTrue(any("print_secrets_expression" in x for x in self.check()[1]))

    def test_unchanged_legacy_risk_allowed_as_debt(self):
        self.wf.write_text("run: printenv\n")
        self.base = self.commit()
        self.wf.write_text("name: legacy\nrun: printenv\n")
        self.commit()
        self.assertEqual(self.check()[1], [])

    def test_replaced_old_dump_denied(self):
        self.wf.write_text("run: printenv\n")
        self.base = self.commit()
        self.wf.write_text("run: env | sort\n")
        self.commit()
        self.assertTrue(self.check()[1])

    def test_added_deep_composite_action_denied(self):
        folder = self.root / "nested" / "deep"
        folder.mkdir(parents=True)
        (folder / "action.yml").write_text("runs:\n  using: composite\n"
                                           "  steps:\n    - run: printenv\n")
        self.commit()
        self.assertTrue(self.check()[1])

    def test_added_safe_root_action(self):
        (self.root / "action.yaml").write_text("runs:\n  using: composite\n"
                                               "  steps:\n    - run: echo safe\n")
        self.commit()
        self.assertEqual(self.check(), (1, []))

    def test_unrelated_file_not_scanned(self):
        (self.root / "README.md").write_text("run: set -x\n")
        self.commit()
        self.assertEqual(self.check(), (0, []))

    def test_symlink_workflow_fails_closed(self):
        self.wf.unlink()
        self.wf.symlink_to(self.root / "missing")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.check()

    def test_nonascii_path_fails_closed(self):
        (self.wf.parent / "é.yml").write_text("run: printenv\n")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.check()

    def test_executable_manifest_fails_closed(self):
        self.wf.chmod(0o755)
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.check()

    def test_invalid_sha_rejected(self):
        with self.assertRaises(gate.Unverifiable):
            gate.changed_paths(self.root, "main;rm -rf", self.base)

    def test_cli_denies_authority(self):
        self.wf.write_text("run: set -x\n")
        head = self.commit()
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = gate.main(["--root", str(self.root), "--base", self.base, "--head", head])
        self.assertEqual(rc, 1)
        self.assertIn("PRODUCTION_MUTATION_AUTHORIZED=false", stream.getvalue())

    def test_non_utf8_blob_fails_closed(self):
        self.wf.write_bytes(b"\xff")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.check()


if __name__ == "__main__":
    unittest.main()
