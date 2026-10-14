"""Adversarial privacy and correctness checks for read-only Worker-v2 metadata."""
import json
import io
from contextlib import redirect_stdout
import os
import time
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

from scripts.zcloud_v2_legacy_service_inventory_20261014 import (
    COMMANDS, _bounded_process_output, _safe_subprocess_env, collect, command_lines, get_background_count, get_source_version,
    main, read_regular, summarize_processes, summarize_units,
)


class LegacyServiceInventoryTests(unittest.TestCase):
    def fake(self, results):
        def execute(argv, **kwargs):
            self.assertEqual(kwargs, dict(capture_output=True, text=True, timeout=5, check=False))
            key = next(name for name, value in COMMANDS.items() if list(value) == argv)
            value = results[key]
            if isinstance(value, Exception):
                raise value
            return types.SimpleNamespace(returncode=0, stdout=value)
        return execute

    def test_bounded_aggregates_never_reveal_private_strings(self):
        marker = "SECRET_USER_CHAT_TITLE_do_not_log"
        with tempfile.TemporaryDirectory() as temp:
            worker = Path(temp) / "worker.user.js"
            worker.write_text("// @name private\n// @version 2.0.3\n" + marker, encoding="utf-8")
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(json.dumps({"background": {"scripts": [marker]}}), encoding="utf-8")
            rows = {
                "process": "firefox\nweb-ext\nssh\n",
                "user_services": "chatgpt-firefox.service loaded active running " + marker,
                "system_services": "zcloud-watchdog.service loaded failed failed " + marker,
                "user_timers": "zcloud-refresh.timer loaded active waiting " + marker,
            }
            report = collect(execute=self.fake(rows), source_path=worker, manifest_path=manifest)
        self.assertTrue(report["observation_complete"])
        self.assertTrue(report["inventory_complete"])
        self.assertEqual(report["missing_components"], [])
        self.assertEqual(report["firefox_process_count"], 2)
        self.assertEqual(report["user_services"]["matching_units"], 1)
        self.assertEqual(report["system_services"]["failed_units"], 1)
        self.assertEqual(report["user_timers"]["active_units"], 1)
        self.assertEqual(report["source_version"], "2.0.3")
        self.assertEqual(report["extension_background_script_count"], 1)
        self.assertNotIn(marker, json.dumps(report))
        self.assertFalse(report["mutation_performed"])
        self.assertFalse(report["production_verified"])
        self.assertFalse(report["worker_generation_verified"])
        self.assertFalse(report["identities_exposed"])

    def test_command_errors_are_unknown_never_zero(self):
        for bad in ("a" * 65537, "a\n" * 4001, "\x00", None):
            with self.subTest(bad=str(bad)[:20]):
                def execute(*a, **k):
                    return types.SimpleNamespace(returncode=0, stdout=bad)
                self.assertIsNone(command_lines(COMMANDS["process"], execute=execute))
        def offline(*a, **k):
            raise subprocess.TimeoutExpired("systemctl", 5)
        self.assertIsNone(command_lines(COMMANDS["user_services"], execute=offline))
        def failed(*a, **k):
            return types.SimpleNamespace(returncode=1, stdout="firefox\n")
        self.assertIsNone(command_lines(COMMANDS["process"], execute=failed))
        self.assertIsNone(summarize_processes(None))
        self.assertIsNone(summarize_processes(["firefox --private Title123"]))
        self.assertIsNone(summarize_units(None, kind="service"))
        self.assertIsNone(summarize_units(["non-systemctl raw private data"], kind="service"))

    def test_unit_parser_ignores_descriptions_and_rejects_identity_ambiguity(self):
        self.assertEqual(summarize_units(["● zcloud.service loaded active running private prompt"], kind="service"),
                         {"matching_units": 1, "active_units": 1, "failed_units": 0})
        self.assertIsNone(summarize_units(["zcloud.service loaded unknown running"], kind="service"))
        self.assertIsNone(summarize_units(["zcloud.service loaded active running",
                                          "zcloud.service loaded active running"], kind="service"))
        self.assertEqual(summarize_units(["unrelated.service loaded active running"], kind="service")["matching_units"], 0)
        self.assertEqual(summarize_units(["zcloud.service loaded active running"], kind="timer")["matching_units"], 0)

    def test_non_regular_or_oversized_or_malformed_source_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            worker = path / "worker.js"
            manifest = path / "manifest.json"
            worker.write_text("// @version 2.0.3\n", encoding="utf-8")
            self.assertEqual(get_source_version(worker), "2.0.3")
            worker.write_text("// @version 2.0.3\n// @version 9.9.9\n", encoding="utf-8")
            self.assertIsNone(get_source_version(worker))
            worker.write_text("// @version private-secret\n", encoding="utf-8")
            self.assertIsNone(get_source_version(worker))
            worker.write_bytes(b"X" * 65537)
            self.assertIsNone(get_source_version(worker))
            worker.unlink()
            worker.symlink_to(path / "private.js")
            self.assertIsNone(get_source_version(worker))
            manifest.write_text('{"background":{"scripts":["x","y"]}}', encoding="utf-8")
            self.assertEqual(get_background_count(manifest), 2)
            manifest.write_text('{"background":{"scripts":"private"}}', encoding="utf-8")
            self.assertIsNone(get_background_count(manifest))
            manifest.write_text('{"background":{"scripts":[1]}}', encoding="utf-8")
            self.assertIsNone(get_background_count(manifest))
            manifest.write_text('{"background":{"scripts":[], "scripts":["x"]}}', encoding="utf-8")
            self.assertIsNone(get_background_count(manifest))
            manifest.write_text('{"background":{"scripts":[]}}', encoding="utf-8")
            self.assertEqual(get_background_count(manifest), 0)
            manifest.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
            self.assertIsNone(get_background_count(manifest))

    def test_main_returns_failure_for_partial_snapshot_without_leaking_raw_error(self):
        payload = {
            "policy": "zcloud-v2-legacy-readonly-inventory-v1",
            "inventory_complete": False,
            "observation_complete": False,
            "missing_components": ["user_services"],
            "mutation_performed": False,
            "production_verified": False,
            "worker_generation_verified": False,
        }
        output = io.StringIO()
        with mock.patch("scripts.zcloud_v2_legacy_service_inventory_20261014.collect", return_value=payload):
            with redirect_stdout(output), self.assertRaises(SystemExit) as outcome:
                main()
        self.assertEqual(outcome.exception.code, 2)
        self.assertIn("ZCLOUD_V2_LEGACY_SAFE_METADATA=", output.getvalue())
        self.assertIn("NO_PROCESS_OR_TAB_MUTATION=1", output.getvalue())
        self.assertNotIn("private-service-path", output.getvalue())

    def test_main_returns_success_only_for_complete_inventory(self):
        payload = {
            "policy": "zcloud-v2-legacy-readonly-inventory-v1",
            "inventory_complete": True,
            "observation_complete": True,
            "missing_components": [],
            "mutation_performed": False,
            "production_verified": False,
            "worker_generation_verified": False,
        }
        output = io.StringIO()
        with mock.patch("scripts.zcloud_v2_legacy_service_inventory_20261014.collect", return_value=payload):
            with redirect_stdout(output):
                self.assertIsNone(main())
        record = output.getvalue().splitlines()[0]
        self.assertTrue(json.loads(record.split("=", 1)[1])["inventory_complete"])
        self.assertIn("NO_PROCESS_OR_TAB_MUTATION=1", output.getvalue())

    def test_missing_component_names_are_allowlisted_not_raw_errors(self):
        marker = "PRIVATE_UNIT_DESCRIPTION_DO_NOT_EXPOSE"
        with tempfile.TemporaryDirectory() as temp:
            rows = {
                "process": "",
                "user_services": marker,
                "system_services": "unknown.service loaded active running",
                "user_timers": "",
            }
            report = collect(
                execute=self.fake(rows),
                source_path=Path(temp) / "missing-script",
                manifest_path=Path(temp) / "missing-manifest",
            )
        self.assertFalse(report["inventory_complete"])
        self.assertIn("user_services", report["missing_components"])
        self.assertNotIn(marker, json.dumps(report))
        self.assertEqual(sorted(report["missing_components"]), report["missing_components"])
        self.assertTrue(set(report["missing_components"]).issubset({
            "process", "user_services", "system_services", "user_timers",
            "source_version", "extension_background_script_count",
        }))

    def test_subprocess_does_not_inherit_runner_secrets_or_mutable_paths(self):
        with mock.patch.dict(os.environ, {
            "GITHUB_TOKEN": "secret",
            "GH_TOKEN": "secret",
            "HOME": "/tmp/attacker",
            "PATH": "/tmp/attacker/bin",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/attacker-bus",
            "LD_PRELOAD": "/tmp/malicious.so",
            "PYTHONPATH": "/tmp/code",
            "SSH_AUTH_SOCK": "/tmp/forwarded-socket",
        }):
            env = _safe_subprocess_env()
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["HOME"], "/home/ubuntu")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertTrue(set(env).issubset({
            "PATH", "HOME", "LANG", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_COLORS", "SYSTEMD_PAGER",
        }))
        self.assertNotIn("secret", repr(env))
        for key in ("LD_PRELOAD", "PYTHONPATH", "SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN"):
            self.assertNotIn(key, env)

    def test_live_command_allowlist_uses_absolute_system_binaries(self):
        # Do not inherit a mutable PATH to resolve live VPS diagnostics.
        self.assertEqual(len(COMMANDS), 4)
        for name, argv in COMMANDS.items():
            with self.subTest(name=name):
                self.assertIn(argv[0], ("/usr/bin/ps", "/usr/bin/systemctl"))
                self.assertTrue(all(isinstance(item, str) for item in argv))
                self.assertNotIn("-c", argv)
                self.assertNotIn("sh", argv)
                self.assertNotIn("bash", argv)

    def test_real_subprocess_streaming_is_bounded_before_decoding(self):
        ok = (sys.executable, "-c", "print('firefox')")
        self.assertEqual(command_lines(ok), ["firefox"])
        huge = (sys.executable, "-c", "import sys; sys.stdout.write('X' * 131072)")
        self.assertIsNone(_bounded_process_output(huge))
        self.assertIsNone(command_lines(huge))
        invalid_utf8 = (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')")
        self.assertIsNone(command_lines(invalid_utf8))
        failing = (sys.executable, "-c", "import sys; print('firefox'); sys.exit(2)")
        self.assertIsNone(command_lines(failing))

    def test_timeout_kills_child_process_group_not_only_parent(self):
        # A child that outlives its timed-out parent could otherwise continue
        # running on the permanent VPS. Test solely in a hosted temp folder.
        with tempfile.TemporaryDirectory() as temporary:
            witness = Path(temporary) / "late-child-witness"
            child = (
                "import pathlib,time,sys;"
                "time.sleep(0.35);"
                "pathlib.Path(sys.argv[1]).write_text('should-never-run')"
            )
            parent = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
                "time.sleep(1)"
            )
            result = _bounded_process_output(
                (sys.executable, "-c", parent, child, str(witness)),
                timeout_seconds=0.08,
            )
            self.assertIsNone(result)
            time.sleep(0.45)
            self.assertFalse(witness.exists(), "timed-out child escaped process group")

    def test_subprocess_deadline_is_fail_closed_and_process_is_reaped(self):
        slow = (sys.executable, "-c", "import time; time.sleep(5); print('firefox')")
        self.assertIsNone(_bounded_process_output(slow, timeout_seconds=0.05))
        self.assertIsNone(_bounded_process_output(slow, timeout_seconds=0))
        self.assertIsNone(_bounded_process_output(slow, timeout_seconds=6))

    def test_readonly_file_rejects_symlink_ancestor_and_relative_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            data = real / "safe.json"
            data.write_text('{"ok":1}', encoding="utf-8")
            self.assertEqual(read_regular(data, 65536), '{"ok":1}')
            (root / "link").symlink_to(real, target_is_directory=True)
            self.assertIsNone(read_regular(root / "link" / "safe.json", 65536))
            self.assertIsNone(read_regular(Path("relative-file"), 65536))
            self.assertIsNone(read_regular(real / ".." / "real" / "safe.json", 65536))

    def test_readonly_file_handles_symlink_fifo_and_invalid_bounds_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.json"
            source.write_text('{"safe":true}', encoding="utf-8")
            self.assertEqual(read_regular(source, 65536), '{"safe":true}')
            self.assertIsNone(read_regular(source, 1))
            self.assertIsNone(read_regular(source, -1))
            self.assertIsNone(read_regular(source, True))
            self.assertIsNone(read_regular(source, 65537))
            link = base / "link"
            link.symlink_to(source)
            self.assertIsNone(read_regular(link, 65536))
            pipe = base / "named-pipe"
            os.mkfifo(pipe)
            self.assertIsNone(read_regular(pipe, 65536))

    def test_readonly_file_rejects_mutation_between_fstat_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "snapshot"
            source.write_text("private content", encoding="utf-8")
            real_fstat = os.fstat
            calls = []

            def fake_fstat(fd):
                original = real_fstat(fd)
                calls.append(original)
                if len(calls) == 2:
                    from types import SimpleNamespace
                    return SimpleNamespace(
                        st_dev=original.st_dev, st_ino=original.st_ino,
                        st_mode=original.st_mode, st_size=original.st_size,
                        st_mtime_ns=original.st_mtime_ns + 1,
                        st_ctime_ns=original.st_ctime_ns,
                    )
                return original

            with mock.patch("scripts.zcloud_v2_legacy_service_inventory_20261014.os.fstat", side_effect=fake_fstat):
                self.assertIsNone(read_regular(source, 65536))
            self.assertEqual(len(calls), 2)

    def test_readonly_file_rejects_nofollow_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "file"
            source.write_text("private data", encoding="utf-8")
            with mock.patch("scripts.zcloud_v2_legacy_service_inventory_20261014.os", spec=[]):
                self.assertIsNone(read_regular(source, 65536))

    def test_partial_evidence_never_claims_full_observation(self):
        rows = {
            "process": "firefox\n",
            "user_services": "zcloud.service loaded active running",
            "system_services": "zcloud.service loaded active running",
            "user_timers": "",
        }
        rows["system_services"] = subprocess.TimeoutExpired("systemctl", 5)
        with tempfile.TemporaryDirectory() as temp:
            report = collect(execute=self.fake(rows), source_path=Path(temp) / "missing",
                             manifest_path=Path(temp) / "missing")
        self.assertFalse(report["observation_complete"])
        self.assertFalse(report["inventory_complete"])
        self.assertEqual(report["missing_components"],
                         ["extension_background_script_count", "source_version", "system_services"])
        self.assertIsNone(report["system_services"])
        self.assertIsNone(report["source_version"])
        self.assertFalse(report["production_verified"])


if __name__ == "__main__":
    unittest.main()
