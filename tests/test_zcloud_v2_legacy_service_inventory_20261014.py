"""Adversarial privacy and correctness checks for read-only Worker-v2 metadata."""
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

from scripts.zcloud_v2_legacy_service_inventory_20261014 import (
    COMMANDS, collect, command_lines, get_background_count, get_source_version,
    summarize_processes, summarize_units,
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
        self.assertIsNone(summarize_units(None, kind="service"))

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
            manifest.write_text('{"background":{"scripts":[]}}', encoding="utf-8")
            self.assertEqual(get_background_count(manifest), 0)

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
