from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ZsshCloudflareCapabilityProbeTests(unittest.TestCase):
    def test_workflow_is_exact_head_vps_bound_reviewed_and_secret_safe(self):
        text = (
            ROOT / ".github/workflows/zssh-cloudflare-capability-probe.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha",
            text,
        )
        self.assertRegex(
            text,
            r"uses: actions/checkout@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("push:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn('".github/zssh-cloudflare-capability-trigger"', text)
        self.assertIn("python3 scripts/zssh_cloudflare_capability_probe.py", text)
        self.assertIn("Upload non-secret capability evidence", text)

        guard = text.index("scripts/zcloud_vps_runner_guard.py --json")
        probe = text.index("python3 scripts/zssh_cloudflare_capability_probe.py")
        self.assertLess(guard, probe)

        self.assertNotIn("secrets.CLOUDFLARE", text)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", text)
        self.assertNotIn("Authorization: Bearer", text)
        self.assertNotIn("set -x", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "rm -",
            "git push",
        ):
            self.assertNotIn(forbidden, text)

    def test_probe_script_is_read_only_and_does_not_emit_secret_material(self):
        text = (
            ROOT / "scripts/zssh_cloudflare_capability_probe.py"
        ).read_text(encoding="utf-8")

        self.assertIn("CLOUDFLARE_API_TOKEN", text)
        self.assertIn("CF_API_TOKEN", text)
        self.assertIn("CLOUDFLARE_ZONE_ID", text)
        self.assertIn("client/v4/user/tokens/verify", text)
        self.assertIn("client/v4/zones", text)
        self.assertIn('method="GET"', text)
        self.assertIn("wrangler whoami", text)
        self.assertIn('"mutation_attempted": False', text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_API_SESSION_PRESENT", text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_WRANGLER_SESSION_PRESENT", text)
        self.assertIn("ZSSH_VPS_CLOUDFLARE_SESSION_ABSENT", text)

        self.assertNotIn('print(token)', text)
        self.assertNotIn('print(zone_id)', text)
        self.assertNotIn('method="POST"', text)
        self.assertNotIn('method="PUT"', text)
        self.assertNotIn('method="PATCH"', text)
        self.assertNotIn('method="DELETE"', text)
        self.assertNotIn("data=", text)


if __name__ == "__main__":
    unittest.main()
