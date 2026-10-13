"""Offline guardrails for worker-only Firefox memory preference management."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_firefox_memory_caps.py"
spec = importlib.util.spec_from_file_location("zcloud_firefox_memory_caps", MODULE)
caps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(caps)


class FirefoxMemoryCapsTests(unittest.TestCase):
    def test_fresh_profile_gets_low_memory_prefs_only(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            with patch.object(caps, "profile_in_use", return_value=False):
                self.assertEqual(caps.apply(profile), "installed-next-start")
            content = (profile / "user.js").read_text()
            self.assertIn('user_pref("image.mem.surfacecache.max_size_kb", 131072);', content)
            self.assertIn('user_pref("browser.cache.memory.capacity", 16384);', content)
            self.assertNotIn("dom.ipc.", content)
            self.assertNotIn("sessionstore", content)
            self.assertNotIn("tabs.unload", content)

    def test_idempotent_and_unmanaged_settings_preserved(self):
        old = '// operator note\nuser_pref("browser.startup.page", 3);\n'
        expected = caps.generate(old)
        self.assertIn(old, expected)
        self.assertEqual(caps.generate(expected), expected)
        self.assertEqual(caps.generate(expected, remove=True), old)

    def test_explicit_custom_override_is_respected(self):
        old = 'user_pref("browser.cache.memory.capacity", 65536);\n'
        installed = caps.generate(old)
        self.assertEqual(installed.count("browser.cache.memory.capacity"), 1)
        self.assertIn(old, installed)

    def test_malformed_markers_refuse_to_mutate(self):
        with self.assertRaises(ValueError):
            caps.generate(caps.BEGIN + "\n")
        with self.assertRaises(ValueError):
            caps.generate(caps.END + "\n" + caps.BEGIN)

    def test_symlink_user_js_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root) / "profile"
            profile.mkdir()
            protected = Path(root) / "other.js"
            protected.write_text("do not change")
            (profile / "user.js").symlink_to(protected)
            with self.assertRaises(ValueError):
                caps.apply(profile)
            self.assertEqual(protected.read_text(), "do not change")

    def test_live_profile_is_never_written(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            with patch.object(caps, "profile_in_use", return_value=True):
                with self.assertRaisesRegex(ValueError, "in use"):
                    caps.apply(profile)
            self.assertFalse((profile / "user.js").exists())

    def test_check_mode_does_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            self.assertEqual(caps.apply(profile, check=True), "would-change")
            self.assertFalse((profile / "user.js").exists())

    def test_remove_preserves_foreign_lines(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            old = '// my prefs\nuser_pref("extensions.activeThemeID", "default-theme@mozilla.org");\n'
            (profile / "user.js").write_text(old)
            with patch.object(caps, "profile_in_use", return_value=False):
                caps.apply(profile)
                self.assertEqual(caps.apply(profile, remove=True), "removed")
            self.assertEqual((profile / "user.js").read_text(), old)


if __name__ == "__main__":
    unittest.main()
