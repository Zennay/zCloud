import pathlib
import re
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class CrashLauncher(unittest.TestCase):
    def test_crash_restarts_with_original_profile_and_live_addon(self):
        source=(ROOT/'server.py').read_text()
        start=source.index('def restart_firefox_runtime():')
        end=source.index('# --- Firefox crash watchdog',start)
        recovery=source[start:end]
        self.assertIn("web_ext=Path.home()/'.local/bin/web-ext'",recovery)
        self.assertIn("addon=Path.home()/'snap/firefox/common/chatgpt-project-extension'",recovery)
        self.assertIn("['run','--source-dir'",recovery.replace("[str(web_ext),'run','--source-dir'","['run','--source-dir'"))
        self.assertIn("'--firefox-profile',str(profile)",recovery)
        self.assertIn("'--keep-profile-changes','--no-reload','--no-input'",recovery)
        self.assertIn("if current.get('active') or existing:",recovery)
        self.assertNotRegex(recovery,r"subprocess\.Popen\(\s*\['/usr/bin/firefox'")

    def test_running_firefox_addon_recovery_is_in_place(self):
        script=(ROOT/'scripts/zcloud_ensure_worker_addon.mjs').read_text()
        self.assertIn("await remote.installTemporaryAddon(addonPath,false)",script)
        self.assertIn("await remote.reloadAddon(addonId)",script)
        self.assertNotIn("browser.tabs.create(",script)
        self.assertNotIn("subprocess.Popen(",script)
        self.assertNotIn("kill -",script)

if __name__=='__main__':
    unittest.main()
