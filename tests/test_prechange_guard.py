import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_prechange_guard.py"
SPEC = importlib.util.spec_from_file_location("zcloud_prechange_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PrechangeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "live"
        self.state = base / "state"
        self.runtime = base / "runtime" / "background.js"
        (self.root / "firefox-extension").mkdir(parents=True)
        (self.root / "public").mkdir(parents=True)
        self.runtime.parent.mkdir(parents=True)
        (self.root / "server.py").write_text("server-v1\n")
        (self.root / "firefox-extension/background.js").write_text("runner-v1\n")
        (self.root / "public/app.js").write_text("app-v1\n")
        self.runtime.write_text("runner-v1\n")
        self.write_lkg()

    def tearDown(self):
        self.tmp.cleanup()

    def write_lkg(self):
        snapshot_id = "test-snapshot"
        files = self.state / "snapshots" / snapshot_id / "files"
        (files / "firefox-extension").mkdir(parents=True)
        (files / "public").mkdir(parents=True)
        (files / "server.py").write_text("server-v1\n")
        (files / "firefox-extension/background.js").write_text("runner-v1\n")
        (files / "public/app.js").write_text("app-v1\n")
        hashes = {
            "server.py": digest(b"server-v1\n"),
            "firefox-extension/background.js": digest(b"runner-v1\n"),
            "public/app.js": digest(b"app-v1\n"),
        }
        manifest = {
            "format_version": 1,
            "snapshot_id": snapshot_id,
            "hashes": hashes,
        }
        (files.parent / "manifest.json").write_text(json.dumps(manifest))
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": snapshot_id})
        )

    def evaluate(self, allowed=None):
        return guard.evaluate(
            self.root,
            self.state,
            self.runtime,
            set(allowed or []),
            live_checks=False,
        )

    def test_green_when_source_matches_lkg_and_runtime(self):
        result = self.evaluate()
        self.assertTrue(result["ok"], result)

    def test_blocks_unexpected_managed_source_change(self):
        (self.root / "server.py").write_text("server-v2\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("server.py", result["unexpected_changes"])

    def test_explicit_allowed_change_does_not_hide_runtime_mismatch(self):
        source = self.root / "firefox-extension/background.js"
        source.write_text("runner-v2\n")
        result = self.evaluate(["firefox-extension/background.js"])
        checks = {c["name"]: c for c in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertEqual([], result["unexpected_changes"])
        self.assertFalse(checks["firefox_source_runtime_match"]["ok"])

    def test_blocks_mixed_firefox_source_runtime_even_if_lkg_matches_source(self):
        self.runtime.write_text("runner-old\n")
        result = self.evaluate()
        checks = {c["name"]: c for c in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["firefox_source_runtime_match"]["ok"])

    def test_blocks_tampered_lkg_snapshot(self):
        snapshot_file = (
            self.state / "snapshots" / "test-snapshot" / "files" / "server.py"
        )
        snapshot_file.write_text("tampered\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertEqual("lkg_integrity", result["checks"][0]["name"])

    def test_exact_legacy_app_backup_is_ignored_but_other_unknown_backups_block(self):
        legacy = self.root / "public/app.js.bak"
        legacy.write_text("legacy-local-backup\n")
        result = self.evaluate()
        self.assertTrue(result["ok"], result)
        self.assertNotIn("public/app.js.bak", result["unexpected_changes"])

        unknown = self.root / "public/other.js.bak"
        unknown.write_text("unknown-local-backup\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("public/other.js.bak", result["unexpected_changes"])

    def test_explicit_violentmonkey_only_dropin_satisfies_live_firefox_guard(self):
        dropin = Path(self.tmp.name) / "10-legacy-disabled.conf"
        dropin.write_text(
            "# Violentmonkey only\n[Service]\nExecCondition=/bin/false\n",
            encoding="utf-8",
        )
        original_service_active = guard.service_active
        original_firefox_runtime_active = guard.firefox_runtime_active
        original_http_healthy = guard.http_healthy
        guard.service_active = lambda service, user=False: service == "zennay-cloud.service"
        guard.firefox_runtime_active = lambda: False
        guard.http_healthy = lambda url, timeout=8.0: True
        try:
            result = guard.evaluate(
                self.root,
                self.state,
                self.runtime,
                set(),
                live_checks=True,
                legacy_disable_path=dropin,
            )
        finally:
            guard.service_active = original_service_active
            guard.firefox_runtime_active = original_firefox_runtime_active
            guard.http_healthy = original_http_healthy

        checks = {item["name"]: item for item in result["checks"]}
        self.assertTrue(result["ok"], result)
        self.assertTrue(checks["firefox_service"]["ok"])
        self.assertEqual(
            {"runtime_active": False, "legacy_violentmonkey_only": True},
            checks["firefox_service"]["detail"],
        )

    def test_missing_or_unmanaged_dropin_does_not_bypass_firefox_guard(self):
        bad_dropin = Path(self.tmp.name) / "bad-disable.conf"
        bad_dropin.write_text("[Service]\nExecCondition=/bin/false\n", encoding="utf-8")
        original_service_active = guard.service_active
        original_firefox_runtime_active = guard.firefox_runtime_active
        original_http_healthy = guard.http_healthy
        guard.service_active = lambda service, user=False: service == "zennay-cloud.service"
        guard.firefox_runtime_active = lambda: False
        guard.http_healthy = lambda url, timeout=8.0: True
        try:
            result = guard.evaluate(
                self.root,
                self.state,
                self.runtime,
                set(),
                live_checks=True,
                legacy_disable_path=bad_dropin,
            )
        finally:
            guard.service_active = original_service_active
            guard.firefox_runtime_active = original_firefox_runtime_active
            guard.http_healthy = original_http_healthy

        checks = {item["name"]: item for item in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["firefox_service"]["ok"])

    def test_generated_python_bytecode_is_ignored_but_unknown_source_still_blocks(self):
        cache = self.root / "scripts" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "worker_scaling_report.cpython-314.pyc").write_bytes(b"generated-bytecode")
        result = self.evaluate()
        self.assertTrue(result["ok"], result)
        self.assertNotIn(
            "scripts/__pycache__/worker_scaling_report.cpython-314.pyc",
            result["unexpected_changes"],
        )

        (cache / "manual_source.py").write_text("manual source\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("scripts/__pycache__/manual_source.py", result["unexpected_changes"])

    def test_historical_lkg_bytecode_does_not_look_like_deleted_source(self):
        snapshot_files = (
            self.state / "snapshots" / "test-snapshot" / "files" / "scripts" / "__pycache__"
        )
        snapshot_files.mkdir(parents=True)
        rel = "scripts/__pycache__/worker_scaling_report.cpython-314.pyc"
        payload = b"old-generated-bytecode"
        (snapshot_files / "worker_scaling_report.cpython-314.pyc").write_bytes(payload)
        manifest_path = self.state / "snapshots" / "test-snapshot" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["hashes"][rel] = digest(payload)
        manifest_path.write_text(json.dumps(manifest))

        result = self.evaluate()
        self.assertTrue(result["ok"], result)
        self.assertNotIn(rel, result["unexpected_changes"])

    def test_unknown_managed_source_reports_only_hash_diagnostics(self):
        path = self.root / "scripts" / "zcloud_governed_exec.py"
        path.parent.mkdir(parents=True)
        path.write_text("unknown-live-source\n")
        result = self.evaluate()
        checks = {item["name"]: item for item in result["checks"]}
        detail = checks["managed_source_state"]["detail"]
        hashes = detail["unexpected_hashes"]["scripts/zcloud_governed_exec.py"]
        self.assertEqual(digest(b"unknown-live-source\n"), hashes["current_sha256"])
        self.assertIsNone(hashes["lkg_sha256"])
        self.assertNotIn("unknown-live-source", json.dumps(detail))

    def test_new_managed_file_is_unexpected(self):
        (self.root / "scripts").mkdir()
        (self.root / "scripts/new_deploy.py").write_text("new\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("scripts/new_deploy.py", result["unexpected_changes"])


    def test_http_health_retries_short_transient_gap(self):
        self.assertEqual(2.0, guard.http_healthy.__defaults__[0])
        with mock.patch.object(
            guard, "http_healthy", side_effect=[False, False, True]
        ) as probe, mock.patch.object(guard.time, "sleep") as sleep:
            self.assertTrue(
                guard.wait_http_healthy(
                    "http://127.0.0.1:8765/",
                    retry_window=10.0,
                    interval=0.5,
                )
            )
        self.assertEqual(3, probe.call_count)
        self.assertEqual(2, sleep.call_count)

    def test_http_health_retry_remains_fail_closed_after_budget(self):
        with mock.patch.object(guard, "http_healthy", return_value=False), \
             mock.patch.object(guard.time, "monotonic", side_effect=[0.0, 4.0, 10.0]), \
             mock.patch.object(guard.time, "sleep"):
            self.assertFalse(
                guard.wait_http_healthy(
                    "http://127.0.0.1:8765/",
                    retry_window=10.0,
                    interval=0.5,
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
