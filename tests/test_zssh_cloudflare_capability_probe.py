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
        self.assertIn("wrangler whoami", text)
        self.assertIn('"mutation_attempted": False', text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_API_SESSION_PRESENT", text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_WRANGLER_SESSION_PRESENT", text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_SESSION_ABSENT", text)

        self.assertNotIn("secrets.CLOUDFLARE", text)
        self.assertNotIn('echo "$token"', text)
        self.assertNotIn("set -x", text)
        self.assertNotIn("--request POST", text)
        self.assertNotIn("--request PUT", text)
        self.assertNotIn("--request PATCH", text)
        self.assertNotIn("--request DELETE", text)
        self.assertNotIn("-X POST", text)
        self.assertNotIn("-X PUT", text)
        self.assertNotIn("-X PATCH", text)
        self.assertNotIn("-X DELETE", text)
        self.assertNotIn("--data", text)


if __name__ == "__main__":
    unittest.main()
