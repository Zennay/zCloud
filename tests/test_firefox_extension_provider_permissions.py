import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FirefoxExtensionProviderPermissionsTest(unittest.TestCase):
    def test_worker_extension_can_recover_chatgpt_and_claude_tabs(self):
        manifest = json.loads((ROOT / "firefox-extension" / "manifest.json").read_text())
        permissions = set(manifest.get("permissions") or [])
        for origin in (
            "https://chatgpt.com/*",
            "https://claude.ai/*",
            "https://claude.com/*",
        ):
            self.assertIn(origin, permissions)


if __name__ == "__main__":
    unittest.main()
