"""Worker browser crash restore: session files survive every restart."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRESTART = ROOT / "deploy/chatgpt-firefox-prestart.sh"
UNIT = ROOT / "deploy/chatgpt-firefox.service"


class FirefoxWorkerCrashRestore(unittest.TestCase):
    def test_prestart_preserves_tabs_and_syncs_extension(self):
        if not shutil.which("rsync"):
            self.skipTest("rsync is required for the on-VPS prestart integration probe")
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            profile = base / "chatgpt-webext-profile"
            source = base / "firefox-extension"
            runtime = base / "chatgpt-project-extension"
            profile.mkdir()
            (profile / "sessionstore-backups").mkdir()
            (source).mkdir()
            (source / "manifest.json").write_text('{"name":"zCloud worker"}')
            # Real Firefox uses this crash-restorable session and its backups.
            (profile / "sessionstore.jsonlz4").write_bytes(b"mock-current-session")
            (profile / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(b"mock-recovery")
            for lock in ("lock", ".parentlock", "parent.lock"):
                (profile / lock).write_text("stale")
            env = dict(os.environ,
                       ZCLOUD_FIREFOX_PROFILE=str(profile),
                       ZCLOUD_FIREFOX_SOURCE=str(source),
                       ZCLOUD_FIREFOX_RUNTIME=str(runtime))
            r = subprocess.run(["bash", str(PRESTART)], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((profile / "sessionstore.jsonlz4").read_bytes(), b"mock-current-session")
            self.assertEqual((profile / "sessionstore-backups" / "recovery.jsonlz4").read_bytes(), b"mock-recovery")
            self.assertTrue((runtime / "manifest.json").is_file())
            for lock in ("lock", ".parentlock", "parent.lock"):
                self.assertFalse((profile / lock).exists())

    def test_restart_does_not_kill_independent_staff_browser(self):
        source = PRESTART.read_text()
        unit = UNIT.read_text()
        self.assertNotIn('"$STOP"', source)
        self.assertNotIn("sessionstore.jsonlz4\"\nrm", source)
        self.assertNotIn("rm -rf \"$PROFILE/sessionstore-backups\"", source)
        self.assertNotIn("ExecStopPost=/home/ubuntu/.local/bin/chatgpt-firefox-stop.sh", unit)
        self.assertIn("KillMode=control-group", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("ExecStartPre=/home/ubuntu/.local/bin/chatgpt-firefox-prestart.sh", unit)


if __name__ == "__main__":
    unittest.main()
