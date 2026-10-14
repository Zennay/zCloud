"""Adversarial privacy and correctness checks for read-only Worker-v2 metadata."""
import json
import os
from unittest import mock
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

from scripts.zcloud_v2_legacy_service_inventory_20261014 import (
    COMMANDS, collect, command_lines, get_background_count, get_source_version,
    read_regular, summarize_processes, summarize_units,
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
        self.assertIsNone(report["system_services"])
        self.assertIsNone(report["source_version"])
        self.assertFalse(report["production_verified"])


if __name__ == "__main__":
    unittest.main()
