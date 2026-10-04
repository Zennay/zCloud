from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ZsshCloudflareCapabilityProbeTests(unittest.TestCase):
    def test_probe_is_vps_bound_read_only_and_secret_safe(self):
        text = (
            ROOT / ".github/workflows/zssh-cloudflare-capability-probe.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("pull_request:", text)
        self.assertIn("types: [closed]", text)
        self.assertIn("github.event.pull_request.merged == true", text)
        self.assertIn('".github/zssh-cloudflare-capability-trigger"', text)

        self.assertIn("CLOUDFLARE_API_TOKEN", text)
        self.assertIn("CF_API_TOKEN", text)
        self.assertIn("CLOUDFLARE_ZONE_ID", text)
        self.assertIn("https://api.cloudflare.com/client/v4/user/tokens/verify", text)
        self.assertIn(
            "https://api.cloudflare.com/client/v4/zones?name=cheapgpt.shop&status=active",
            text,
        )
        self.assertIn("scripts/zssh_cloudflare_capability_probe.py", text)
        script = (ROOT / "scripts/zssh_cloudflare_capability_probe.py").read_text(encoding="utf-8")
        compile(script, "zssh_cloudflare_capability_probe.py", "exec")
        self.assertIn("wrangler whoami", script)
        self.assertIn('"mutation_attempted": False', script)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_API_SESSION_PRESENT", script)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_WRANGLER_SESSION_PRESENT", script)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_SESSION_ABSENT", script)
        self.assertNotIn("\\nimport json\\n", text)
        self.assertNotIn("<<", text)

        self.assertNotIn("secrets.CLOUDFLARE", text)
        self.assertNotIn('echo "$token"', script)
        self.assertNotIn("set -x", script)
        self.assertNotIn("--request POST", script)
        self.assertNotIn("--request PUT", script)
        self.assertNotIn("--request PATCH", script)
        self.assertNotIn("--request DELETE", script)
        self.assertNotIn("-X POST", script)
        self.assertNotIn("-X PUT", script)
        self.assertNotIn("-X PATCH", script)
        self.assertNotIn("-X DELETE", script)
        self.assertNotIn("--data", script)


if __name__ == "__main__":
    unittest.main()

    def test_probe_script_classifies_capabilities_without_returning_secret_values(self):
        import importlib.util

        path = ROOT / "scripts/zssh_cloudflare_capability_probe.py"
        spec = importlib.util.spec_from_file_location("zssh_cloudflare_capability_probe", path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        seen = []

        def fake_run(command, *, timeout):
            seen.append((command, timeout))
            if "CLOUDFLARE_API_TOKEN" in command and command.startswith("test -n"):
                return True
            if "CLOUDFLARE_ZONE_ID" in command and command.startswith("test -n"):
                return False
            if "user/tokens/verify" in command:
                return True
            if "zones?name=cheapgpt.shop" in command:
                return True
            if "command -v wrangler" in command:
                return False
            if "command -v cloudflared" in command:
                return True
            return False

        result = module.probe(fake_run)
        self.assertTrue(result["persistent_token_present"])
        self.assertFalse(result["persistent_zone_id_present"])
        self.assertTrue(result["token_verified"])
        self.assertTrue(result["zone_readable"])
        self.assertTrue(result["persistent_provider_session_present"])
        self.assertFalse(result["mutation_attempted"])
        serialized = str(result)
        self.assertNotIn("Bearer", serialized)
        self.assertNotIn("token=", serialized)
