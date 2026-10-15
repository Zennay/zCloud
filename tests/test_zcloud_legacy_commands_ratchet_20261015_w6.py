"""Real temporary-Git regressions for the legacy GitHub Actions command ratchet."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_legacy_commands_ratchet_20261015_w6.py"
SPEC = importlib.util.spec_from_file_location("legacy_command_gate", SOURCE)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


class RealGitRatchetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.oldcwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.oldcwd)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI")
        self.write(".github/workflows/old.yml", "name: existing\non: push\njobs: {}\n")
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *argv):
        return subprocess.check_output(["git", *argv], stderr=subprocess.DEVNULL)

    def write(self, path, data):
        f = Path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data if isinstance(data, bytes) else data.encode())

    def commit(self, message="fixture"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def test_unchanged_legacy_allowed_without_certification(self):
        self.write(".github/workflows/old.yml", "name: legacy\nrun: echo '::set-env name=X::Y'\n")
        self.commit("legacy")
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.write("README.md", "unrelated\n")
        head = self.commit("unrelated")
        self.assertEqual(gate.scan(base, head), (0, 0))

    def test_changed_legacy_becomes_denied(self):
        self.write(".github/workflows/old.yml", "run: echo '::set-env name=X::Y'\n")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_safe_new_workflow_passes(self):
        self.write(".github/workflows/new.yml", "name: safe\non: pull_request\njobs: {}\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_safe_edited_workflow_passes(self):
        self.write(".github/workflows/old.yml", "name: safe\non: push\njobs: {}\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_legacy_commands_deny(self):
        for forbidden in ("::set-env", "::add-path", "::set-output", "::save-state",
                          ":: SET-ENV", "ACTIONS_ALLOW_UNSECURE_COMMANDS",
                          "actions_allow_unsecure_commands"):
            with self.subTest(forbidden=forbidden):
                self.git("reset", "--hard", self.base)
                self.write(".github/workflows/old.yml", "run: echo '" + forbidden + " name=x::hi'\n")
                with self.assertRaises(gate.Denied):
                    gate.scan(self.base, self.commit())

    def test_a_github_env_write_is_not_deprecated_command(self):
        self.write(".github/workflows/old.yml", "run: echo HELLO=world >> ${GITHUB_ENV}\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_safe_composite_action_passes(self):
        self.write(".github/actions/tool/action.yml", "name: safe\nruns:\n  using: composite\n  steps: []\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_composite_action_at_actions_root_is_checked(self):
        self.write(".github/actions/action.yml", "name: safe\n")
        safe = self.commit()
        self.assertEqual(gate.scan(self.base, safe), (1, 1))
        self.write(".github/actions/action.yml", "run: echo '::set-env name=x::y'\n")
        with self.assertRaises(gate.Denied):
            gate.scan(safe, self.commit())

    def test_unsafe_composite_action_denied(self):
        self.write(".github/actions/tool/action.yaml", "run: echo '::set-output name=X::Y'\n")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_raw_ascii_controls_in_changed_source_are_denied(self):
        for control in (b"\x00", b"\x01", b"\x07", b"\x08", b"\x1b", b"\x7f"):
            with self.subTest(control=control):
                self.git("reset", "--hard", self.base)
                self.write(".github/workflows/old.yml", b"name: safe" + control + b"label\n")
                with self.assertRaisesRegex(gate.Denied, "control character"):
                    gate.scan(self.base, self.commit())

    def test_yaml_unicode_escaped_controls_are_denied(self):
        for value in (r"\x00", r"\u0001", r"\u001b", r"\u007f", r"\u0085"):
            with self.subTest(value=value):
                self.git("reset", "--hard", self.base)
                self.write(".github/workflows/old.yml", 'name: "safe' + value + 'label"\n')
                with self.assertRaisesRegex(gate.Denied, "control character"):
                    gate.scan(self.base, self.commit())

    def test_safe_line_breaks_tabs_and_crlf_remain_valid(self):
        self.write(".github/workflows/old.yml",
                   "name: safe\r\non: push\r\nrun: |\r\n\t echo normal\r\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_changed_workflow_path_with_control_character_denied(self):
        self.write(".github/workflows/bad\x01name.yml", "name: safe\n")
        with self.assertRaisesRegex(gate.Denied, "control-bearing workflow pathname"):
            gate.scan(self.base, self.commit())

    def test_changed_action_path_with_unicode_format_control_denied(self):
        self.write(".github/actions/tool/\u202eaction.yml", "name: safe\n")
        with self.assertRaisesRegex(gate.Denied, "control-bearing workflow pathname"):
            gate.scan(self.base, self.commit())

    def test_non_utf8_denied(self):
        self.write(".github/workflows/old.yml", b"name: \xff\n")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_oversized_workflow_denied(self):
        self.write(".github/workflows/old.yml", "x" * (gate.MAX_BLOB + 1))
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_unicode_format_controls_denied(self):
        self.write(".github/workflows/old.yml", "name: \u202eevil\n")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_extensionless_executable_action_source_is_denied(self):
        self.write(".github/actions/tool/bin/launch", "echo '::set-output name=K::V'\n")
        Path(".github/actions/tool/bin/launch").chmod(0o755)
        with self.assertRaisesRegex(gate.Denied, "legacy unsafe"):
            gate.scan(self.base, self.commit())

    def test_extensionless_safe_executable_is_checked(self):
        self.write(".github/actions/tool/bin/launch", "echo safe\n")
        Path(".github/actions/tool/bin/launch").chmod(0o755)
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_named_extensionless_entrypoint_is_scanned_without_exec_bit(self):
        self.write(".github/actions/tool/entrypoint", "echo '::save-state name=K::V'\n")
        with self.assertRaisesRegex(gate.Denied, "legacy unsafe"):
            gate.scan(self.base, self.commit())

    def test_makefile_and_windows_action_scripts_are_scanned(self):
        for path in (".github/actions/tool/Makefile", ".github/actions/tool/GNUmakefile",
                     ".github/actions/tool/setup.cmd", ".github/actions/tool/setup.bat",
                     ".github/actions/tool/setup.zsh", ".github/actions/tool/setup.rb",
                     ".github/actions/tool/setup.pl"):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.write(path, "echo '::set-env name=X::Y'\n")
                with self.assertRaisesRegex(gate.Denied, "legacy unsafe"):
                    gate.scan(self.base, self.commit())

    def test_package_json_action_lifecycle_script_is_checked(self):
        self.write(".github/actions/tool/package.json",
                   '{"scripts":{"prepare":"echo ::set-output name=X::Y"}}\n')
        with self.assertRaisesRegex(gate.Denied, "legacy unsafe"):
            gate.scan(self.base, self.commit())

    def test_safe_package_json_action_lifecycle_script_passes(self):
        self.write(".github/actions/tool/package.json",
                   '{"scripts":{"prepare":"echo safe"}}\n')
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_cmd_caret_escaped_commands_are_denied(self):
        for value in ("::set-^output", "::set^-env", "::save-^state",
                      "ACTIONS_ALLOW_UNSECURE_^COMMANDS"):
            with self.subTest(value=value):
                self.git("reset", "--hard", self.base)
                self.write(".github/actions/tool/activate.cmd", "echo " + value + "\n")
                with self.assertRaisesRegex(gate.Denied, "CMD caret-escaped"):
                    gate.scan(self.base, self.commit())

    def test_posix_backslash_escaped_logging_tokens_are_denied(self):
        for value in (r"::set\-output", r"::set-out\put", r"::add\-path",
                      r"ACTIONS_ALLOW_UNSECURE_\COMMANDS"):
            with self.subTest(value=value):
                self.git("reset", "--hard", self.base)
                self.write(".github/actions/tool/run.sh", "echo " + value + "\n")
                with self.assertRaisesRegex(gate.Denied, "Escaped legacy command tokens"):
                    gate.scan(self.base, self.commit())

    def test_mixed_posix_and_cmd_escaping_cannot_hide_logging_command(self):
        self.write(".github/actions/tool/run.sh", r"echo ::set\-^output" + "\n")
        with self.assertRaisesRegex(gate.Denied, "Escaped legacy command tokens"):
            gate.scan(self.base, self.commit())

    def test_safe_backslash_escaped_action_text_is_accepted(self):
        self.write(".github/actions/tool/run.sh", r"echo C:\Users\public" + "\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_powershell_backtick_escaped_command_is_denied(self):
        for value in ("::set-`output", "::save-`state",
                      "ACTIONS_ALLOW_UNSECURE_`COMMANDS"):
            with self.subTest(value=value):
                self.git("reset", "--hard", self.base)
                self.write(".github/actions/tool/run.ps1", 'Write-Output "' + value + '"\n')
                with self.assertRaisesRegex(gate.Denied, "PowerShell backtick-escaped"):
                    gate.scan(self.base, self.commit())

    def test_powershell_backtick_line_continuation_is_denied(self):
        self.write(".github/actions/tool/run.ps1",
                   'Write-Output "::set-`\r\noutput name=K::V"\r\n')
        with self.assertRaisesRegex(gate.Denied, "PowerShell backtick-escaped"):
            gate.scan(self.base, self.commit())

    def test_safe_powershell_escaped_text_is_allowed(self):
        self.write(".github/actions/tool/run.ps1", 'Write-Output "hello`world"\n')
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_safe_github_output_file_write_is_not_deprecated_command(self):
        self.write(".github/actions/tool/run.sh", 'echo "VALUE=ok" >> "$GITHUB_OUTPUT"\n')
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_safe_cmd_caret_escaping_is_allowed(self):
        self.write(".github/actions/tool/activate.cmd", "echo a^safe-value\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_non_executable_unrecognized_extensionless_action_file_is_not_executable_source(self):
        self.write(".github/actions/tool/reference-data", "safe example text\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 0))

    def test_changed_local_action_implementation_sources_are_scanned(self):
        unsafe = {
            ".github/actions/tool/run.sh": "echo '::set-output name=X::Y'\\n",
            ".github/actions/tool/src/index.js": "console.log('::add-path::/tmp/evil');\\n",
            ".github/actions/tool/src/main.ts": "console.log('::save-state name=K::V');\\n",
            ".github/actions/tool/Containerfile": "RUN echo '::set-env name=K::V'\\n",
            ".github/actions/tool/script.py": "print('::set-env name=K::V')\\n",
        }
        for path, source in unsafe.items():
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.write(path, source)
                with self.assertRaisesRegex(gate.Denied, "legacy unsafe"):
                    gate.scan(self.base, self.commit())

    def test_safe_action_implementation_scripts_count_as_checked(self):
        self.write(".github/actions/tool/src/index.js", "console.log('hello');\\n")
        self.write(".github/actions/tool/run.sh", "echo safe\\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (2, 2))

    def test_changed_action_scripts_fail_closed_when_oversized(self):
        self.write(".github/actions/tool/run.sh", "x" * (gate.MAX_BLOB + 1))
        with self.assertRaisesRegex(gate.Denied, "size limit"):
            gate.scan(self.base, self.commit())

    def test_deleted_action_implementation_does_not_reintroduce_debt(self):
        self.write(".github/actions/tool/run.sh", "echo '::set-output name=K::V'\\n")
        self.write(".github/actions/tool/readme.txt", "kept\\n")
        original = self.commit()
        Path(".github/actions/tool/run.sh").unlink()
        after = self.commit()
        self.assertEqual(gate.scan(original, after), (0, 0))

    def test_new_gitlink_under_actions_is_denied_even_without_metadata_file(self):
        # Gitlinks are opaque references: checking only action.yml names misses
        # newly introduced third-party execution sources.
        self.git("update-index", "--add", "--cacheinfo",
                 "160000," + self.base + ",.github/actions/external")
        self.git("commit", "-qm", "add gitlink")
        head = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(gate.Denied, "unsafe Git tree entry"):
            gate.scan(self.base, head)

    def test_new_non_metadata_symlink_under_actions_is_denied(self):
        Path(".github/actions/tool").mkdir(parents=True, exist_ok=True)
        os.symlink("/tmp/untrusted", ".github/actions/tool/README.md")
        with self.assertRaisesRegex(gate.Denied, "unsafe Git tree entry"):
            gate.scan(self.base, self.commit())

    def test_regular_non_metadata_action_file_remains_allowed(self):
        self.write(".github/actions/tool/README.md", "plain ordinary readme\\n")
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 0))

    def test_deleted_action_symlink_does_not_introduce_unsafe_source(self):
        Path(".github/actions/tool").mkdir(parents=True, exist_ok=True)
        os.symlink("/tmp/untrusted", ".github/actions/tool/README.md")
        before = self.commit()
        Path(".github/actions/tool/README.md").unlink()
        self.write(".github/actions/tool/notes.txt", "safe\\n")
        after = self.commit()
        self.assertEqual(gate.scan(before, after), (1, 0))

    def test_symlink_workflow_denied(self):
        Path(".github/workflows/old.yml").unlink()
        os.symlink("/tmp/untrusted", ".github/workflows/old.yml")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_symlink_workflow_root_denied(self):
        Path(".github/workflows/old.yml").unlink()
        Path(".github/workflows").rmdir()
        os.symlink("/tmp/untrusted", ".github/workflows")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_actions_root_symlink_denied(self):
        os.symlink("/tmp/untrusted", ".github/actions")
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, self.commit())

    def test_sha_format_denied(self):
        with self.assertRaises(gate.Denied):
            gate.scan("HEAD", self.base)

    def test_disconnected_base_denied(self):
        self.git("checkout", "--orphan", "unrelated")
        self.write(".github/workflows/new.yml", "name: other\n")
        other = self.commit()
        with self.assertRaises(gate.Denied):
            gate.scan(self.base, other)

    def test_deleted_legacy_file_does_not_create_new_debt(self):
        self.write(".github/workflows/another.yml", "name: kept\n")
        self.write(".github/workflows/old.yml", "run: echo '::set-env name=X::Y'\n")
        original = self.commit()
        Path(".github/workflows/old.yml").unlink()
        after = self.commit()
        self.assertEqual(gate.scan(original, after), (0, 0))


    def test_yaml_escaped_unsafe_command_denied(self):
        examples = (
            r"::set\u002doutput",
            r"::set\x2denv",
            r"::add\u002dpath",
            r"ACTIONS_ALLOW_UNSECURE_COMM\u0041NDS",
            r"::set\u005cu002doutput",
        )
        for value in examples:
            with self.subTest(value=value):
                self.git("reset", "--hard", self.base)
                self.write(".github/workflows/old.yml", 'run: "echo ' + value + '"\n')
                with self.assertRaises(gate.Denied):
                    gate.scan(self.base, self.commit())

    def fake_git(self, program: str):
        bindir = Path(self.temp.name) / "fakebin"
        bindir.mkdir(exist_ok=True)
        target = bindir / "git"
        target.write_text("#!/usr/bin/env python3\n" + program)
        target.chmod(0o755)
        return str(bindir) + os.pathsep + os.environ["PATH"]

    def test_git_stdout_flood_is_killed_within_budget(self):
        path = self.fake_git("import sys\nsys.stdout.buffer.write(b'x' * 4096)\n")
        with mock.patch.dict(os.environ, {"PATH": path}):
            with self.assertRaises(gate.Denied):
                gate.git("version", max_output=128)

    def test_git_hang_is_killed_after_timeout(self):
        path = self.fake_git("import time\ntime.sleep(10)\n")
        with mock.patch.dict(os.environ, {"PATH": path}), mock.patch.object(gate, "TIMEOUT", 0.2):
            with self.assertRaises(gate.Denied):
                gate.git("version", max_output=128)

    def test_git_forked_descendant_cannot_hold_stdout_open_after_parent_exits(self):
        # Parent Git wrapper exits but a descendant inherits stdout. Even
        # after that parent exits, the group must die before writing a marker.
        marker = Path(self.temp.name) / "orphan-survived"
        path = self.fake_git(
            "import os, time\n"
            "if os.fork() == 0:\n"
            "    time.sleep(0.7)\n"
            "    open(os.environ['GATE_MARKER_PATH'], 'w').write('survived')\n"
            "    os._exit(0)\n"
            "os._exit(0)\n"
        )
        with (mock.patch.dict(os.environ, {"PATH": path, "GATE_MARKER_PATH": str(marker)}),
              mock.patch.object(gate, "TIMEOUT", 0.2),
              mock.patch.object(gate.os, "killpg", wraps=gate.os.killpg) as killed):
            with self.assertRaises(gate.Denied):
                gate.git("version", max_output=128)
            self.assertTrue(killed.called, "orphaned Git process group must be killed")
        time.sleep(0.8)
        self.assertFalse(marker.exists(), "Git descendant survived aborted source proof")

    def test_successful_git_read_never_kills_process_group(self):
        with mock.patch.object(gate.os, "killpg", wraps=gate.os.killpg) as kill_group:
            version = gate.git("--version")
            self.assertIn(b"git version", version)
            kill_group.assert_not_called()

    def test_invalid_output_budget_is_denied(self):
        for budget in (0, -1, gate.MAX_OUTPUT + 1):
            with self.subTest(budget=budget), self.assertRaises(gate.Denied):
                gate.git("version", max_output=budget)


    def test_shared_scan_budget_denies_before_excessive_git_invocations(self):
        with mock.patch.object(gate, "MAX_SCAN_SECONDS", 0.001):
            with self.assertRaises(gate.Denied):
                gate.scan(self.base, self.base)
        self.assertIsNone(gate._SCAN_DEADLINE.get())

    def test_exhausted_scan_budget_does_not_launch_git(self):
        token = gate._SCAN_DEADLINE.set(time.monotonic() - 1.0)
        try:
            with mock.patch.object(gate.subprocess, "Popen", side_effect=AssertionError("Git launched")):
                with self.assertRaisesRegex(gate.Denied, "budget exhausted"):
                    gate.git("status")
        finally:
            gate._SCAN_DEADLINE.reset(token)

    def test_shared_scan_budget_validates_limits(self):
        for limit in (0, -1, 3601):
            with self.subTest(limit=limit), mock.patch.object(gate, "MAX_SCAN_SECONDS", limit):
                with self.assertRaisesRegex(gate.Denied, "Invalid shared scan budget"):
                    gate.scan(self.base, self.base)
        self.assertIsNone(gate._SCAN_DEADLINE.get())

    def test_successful_scan_restores_context_budget(self):
        self.assertEqual(gate.scan(self.base, self.base), (0, 0))
        self.assertIsNone(gate._SCAN_DEADLINE.get())


    def test_shell_continued_deprecated_command_is_denied(self):
        self.write(".github/workflows/old.yml",
                   'run: |\n  echo "::set-\\\n    output name=KEY::value"\n')
        with self.assertRaisesRegex(gate.Denied, "Shell-continued"):
            gate.scan(self.base, self.commit())

    def test_shell_continued_command_with_crlf_is_denied(self):
        self.write(".github/workflows/old.yml",
                   'run: |\r\n  echo "::save-\\\r\n    state name=KEY::value"\r\n')
        with self.assertRaisesRegex(gate.Denied, "Shell-continued"):
            gate.scan(self.base, self.commit())

    def test_yaml_escaped_shell_continuation_is_denied(self):
        self.write(".github/workflows/old.yml",
                   r'run: "echo ::set-\u005c' + '\n    env name=KEY::value"\n')
        with self.assertRaisesRegex(gate.Denied, "Shell-continued"):
            gate.scan(self.base, self.commit())

    def test_benign_shell_continuation_is_allowed(self):
        self.write(".github/workflows/old.yml",
                   'run: |\n  echo "safe-\\\n    output"\n')
        self.assertEqual(gate.scan(self.base, self.commit()), (1, 1))

    def test_invalid_unicode_scalar_escapes_fail_closed(self):
        for escaped in (r"\U00110000", r"\UFFFFFFFF", r"\uD800", r"\uDC00"):
            with self.subTest(escaped=escaped), self.assertRaises(gate.Denied):
                gate.check_content(("name: " + escaped + "\n").encode("utf-8"))

    def test_escaped_unicode_format_controls_fail_closed(self):
        with self.assertRaisesRegex(gate.Denied, "control character"):
            gate.check_content(r"name: \u202e".encode("utf-8"))

    def test_excessively_nested_unicode_escapes_fail_closed(self):
        value = r"::set\u002doutput"
        for _ in range(10):
            value = value.replace("\\", r"\u005c")
        with self.assertRaisesRegex(gate.Denied, "nested"):
            gate.check_content(value.encode("utf-8"))

    def test_safe_unicode_scalar_escape_is_allowed(self):
        gate.check_content(r"name: hell\u006f".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
