"""Adversarial tests for read-only workflow secret log exposure policy."""
import io
import os
import time
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import zcloud_secret_log_exposure_ratchet_20261015_w1 as gate


class HostedWorkflowTrust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-secret-log-exposure-ratchet-20261015-w1.yml"
        ).read_text(encoding="utf-8")

    def test_hosted_only_and_read_only(self):
        self.assertEqual(self.source.count("runs-on: ubuntu-latest"), 2)
        self.assertIn("contents: read", self.source)
        for unsafe in ("runs-on: self-hosted", "contents: write",
                       "actions: write", "sudo ", "workflow_dispatch:",
                       "gh workflow run", "systemctl restart"):
            self.assertNotIn(unsafe, self.source)

    def test_immutable_checkout_without_credentials(self):
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
                      self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', self.source)

    def test_main_equality_before_and_after(self):
        statement = "timeout 15 git ls-remote --exit-code origin refs/heads/main"
        self.assertEqual(self.source.count(statement), 2)
        self.assertEqual(self.source.count('test "$remote_main" = "$BASE_SHA"'), 2)

    def test_trust_admission_prior_to_checkout(self):
        self.assertIn('test "$HEAD_REPOSITORY" = "$CURRENT_REPOSITORY"', self.source)
        self.assertIn('test "$BASE_REPOSITORY" = "$CURRENT_REPOSITORY"', self.source)
        self.assertIn('test "$BASE_BRANCH" = main', self.source)

    def test_non_authorizing_output(self):
        self.assertIn("Production mutation authorized: false", self.source)


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

    def test_log_whole_secrets_context(self):
        line = "run: echo " + "$" + "{{ toJSON(secrets) }}"
        self.assertEqual(self.hits(line, "print_sensitive_context_json"), 1)

    def test_log_whole_github_context(self):
        line = "run: printf %s " + "$" + "{{ toJson(github) }}"
        self.assertEqual(self.hits(line, "print_sensitive_context_json"), 1)

    def test_log_whole_env_context(self):
        line = "run: echo " + "$" + "{{ toJSON(env) }}"
        self.assertEqual(self.hits(line, "print_sensitive_context_json"), 1)

    def test_safe_json_of_neutral_workflow_metadata(self):
        line = "run: echo " + "$" + "{{ toJSON(matrix.os) }}"
        self.assertEqual(self.hits(line, "print_sensitive_context_json"), 0)

    def test_log_indexed_secret_expression(self):
        line = "run: Write-Host " + "$" + "{{ secrets['RELEASE_TOKEN'] }}"
        self.assertEqual(self.hits(line, "print_indexed_secret_expression"), 1)

    def test_printenv_named_token(self):
        self.assertEqual(self.hits("run: printenv GITHUB_TOKEN",
                                   "print_named_secret_env"), 1)

    def test_printenv_unrelated_value(self):
        self.assertEqual(self.hits("run: printenv CI",
                                   "print_named_secret_env"), 0)

    def test_cat_github_output(self):
        line = 'run: cat "' + chr(36) + '{GITHUB_OUTPUT}"'
        self.assertEqual(self.hits(line, "print_github_env_file"), 1)

    def test_cat_dotenv_local(self):
        self.assertEqual(self.hits("run: cat config/.env.local", "print_dotenv_file"), 1)

    def test_dotenv_sample_not_a_live_secret_file(self):
        self.assertEqual(self.hits("run: cat .env.example", "print_dotenv_file"), 0)

    def test_cat_aws_credentials(self):
        self.assertEqual(self.hits("run: cat ~/.aws/credentials", "print_credential_file"), 1)

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

    def test_new_sensitive_context_echo_denied(self):
        self.wf.write_text("run: echo " + "$" + "{{ toJSON(secrets) }}\n")
        self.commit()
        self.assertTrue(any("print_sensitive_context_json" in x for x in self.check()[1]))

    def test_new_dotenv_dump_denied(self):
        self.wf.write_text("run: cat .env.production\n")
        self.commit()
        self.assertTrue(any("print_dotenv_file" in x for x in self.check()[1]))

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

    def test_git_output_limit_fails_closed(self):
        fake = self.root / "git"
        fake.write_text("#!/usr/bin/env python3\n"
                        "import sys\nsys.stdout.write('A'*230000)\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaises(gate.Unverifiable):
                gate.git(self.root, "status", deadline_seconds=1.0)

    def test_git_deadline_fails_closed(self):
        fake = self.root / "git"
        fake.write_text("#!/usr/bin/env python3\n"
                        "import time\ntime.sleep(5)\n")
        fake.chmod(0o755)
        start = time.monotonic()
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaises(gate.Unverifiable):
                gate.git(self.root, "status", deadline_seconds=0.2)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_git_subprocess_inherited_stdout_reaped(self):
        fake = self.root / "git"
        marker = self.root / "orphan"
        code = ("import time\n"
                "time.sleep(0.7)\n"
                f"open({str(marker)!r}, 'w').write('orphan')\n")
        fake.write_text("#!/usr/bin/env python3\n"
                        "import subprocess, sys\n"
                        f"subprocess.Popen([sys.executable, '-c', {code!r}])\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaises(gate.Unverifiable):
                gate.git(self.root, "status", deadline_seconds=0.2)
        time.sleep(0.85)
        self.assertFalse(marker.exists())

    def test_non_utf8_blob_fails_closed(self):
        self.wf.write_bytes(b"\xff")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.check()


if __name__ == "__main__":
    unittest.main()
