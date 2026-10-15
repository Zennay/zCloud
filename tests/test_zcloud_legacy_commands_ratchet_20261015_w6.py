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


if __name__ == "__main__":
    unittest.main()
