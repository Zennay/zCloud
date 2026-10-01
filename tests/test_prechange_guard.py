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

    def test_new_managed_file_is_unexpected(self):
        (self.root / "scripts").mkdir()
        (self.root / "scripts/new_deploy.py").write_text("new\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("scripts/new_deploy.py", result["unexpected_changes"])

    def test_http_health_retries_short_transient_gap(self):
        with mock.patch.object(
            guard, "http_healthy", side_effect=[False, False, True]
        ) as probe, mock.patch.object(guard.time, "sleep") as sleep:
            self.assertTrue(
                guard.wait_http_healthy(
                    "http://127.0.0.1:8765/api/status",
                    retry_window=10.0,
                    interval=0.5,
                )
            )
        self.assertEqual(3, probe.call_count)
        self.assertEqual(2, sleep.call_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
