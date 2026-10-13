"""Contract: VPS-hosted relay must not hijack zCloud worker Firefox."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RelayVPSLauncherTests(unittest.TestCase):
    def test_separate_vps_firefox_profile(self):
        launcher = (ROOT / "office-relay-vps.sh").read_text()
        self.assertIn("office-relay-vps-profile", launcher)
        self.assertIn("snap/firefox/common", launcher)
        self.assertIn('exec "$browser" --no-remote --profile "$profile"', launcher)
        self.assertIn('DISPLAY="${OFFICE_RELAY_DISPLAY:-:10}"', launcher)
        self.assertIn("De VPS RDP-desktop", launcher)
        self.assertNotIn("chatgpt-webext-profile", launcher)
        self.assertNotIn("systemctl stop chatgpt-firefox", launcher)
        self.assertNotIn("OFFICE_ACCESS_TOKEN", launcher)
        self.assertNotIn("198.244.191.182:3422", launcher)

    def test_only_saved_chatgpt_conversations_are_opened(self):
        launcher = (ROOT / "office-relay-vps.sh").read_text()
        self.assertIn("relay-tabs.txt", launcher)
        self.assertIn("https://chatgpt\\.com/c/", launcher)
        self.assertIn("< 3", launcher)

    def test_service_is_user_scoped(self):
        service = (ROOT / "office-relay-vps.service").read_text()
        self.assertIn("ExecStart=/bin/bash /opt/virtual-office/office-relay-vps.sh", service)
        self.assertIn("DISPLAY=:10", service)
        self.assertIn("Restart=always", service)
        self.assertNotIn("User=root", service)
        self.assertNotIn("chatgpt-firefox.service", service)


if __name__ == "__main__":
    unittest.main()
