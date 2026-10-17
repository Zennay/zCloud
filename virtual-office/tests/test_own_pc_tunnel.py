"""Static safety/behavior contracts for PC-hosted Tampermonkey to VPS via SSH."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PCTunnelContractTests(unittest.TestCase):
    def test_both_tunnel_launchers_bind_pc_loopback_only(self):
        for filename in ("connect-from-pc.sh", "connect-from-pc.ps1"):
            with self.subTest(filename=filename):
                script = (ROOT / filename).read_text()
                self.assertIn("127.0.0.1:3422:127.0.0.1:3422", script)
                self.assertIn("ExitOnForwardFailure=yes", script)
                self.assertIn("ServerAliveInterval=25", script)
                self.assertNotIn("StrictHostKeyChecking=no", script)
                self.assertNotIn("198.244.191.182:3422", script)

    def test_userscript_probes_office_before_reading_token(self):
        script = (ROOT / "office-relay.user.js").read_text()
        self.assertIn("@match        https://chatgpt.com/*", script)
        self.assertIn("method:'GET',url:origin+'/api/health'", script)
        self.assertIn("info?.mode!=='zcloud_chatgpt_chats'", script)
        self.assertLess(script.index("await checkOffice(u.origin)"),
                        script.index("const token=prompt("))
        self.assertIn("u.protocol==='http:'", script)
        self.assertIn("'127.0.0.1','localhost'", script)

    def test_own_pc_onboarding_mentions_safe_endpoint(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("your own PC", readme)
        self.assertIn("Tampermonkey", readme)
        self.assertIn("http://127.0.0.1:3422/api/health", readme)
        self.assertIn("YOUR_SSH_USER@198.244.191.182", readme)
        self.assertIn("native ChatGPT desktop application", readme)


if __name__ == "__main__":
    unittest.main()
