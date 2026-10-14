from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-cloudflare-credential-probe.yml"


class ZsshCloudflareCredentialProbeTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_live_probe_is_main_only_exact_revision_and_permanent_vps_bound(self) -> None:
        text = self.text
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("ZCLOUD_EXPECTED_SHA: ${{ github.sha }}", text)
        self.assertRegex(
            text,
            r"uses: actions/checkout@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("clean: true", text)
        self.assertIn("fetch-depth: 1", text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"',
            text,
        )
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)

        exact_revision = text.index('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"')
        gh_metadata = text.index("gh auth status --hostname github.com")
        self.assertLess(exact_revision, gh_metadata)

    def test_probe_keeps_secret_values_out_and_bounds_action_logs(self) -> None:
        text = self.text
        self.assertIn("secret_values_read", text)
        self.assertIn('"secret_values_read": False', text)
        self.assertIn("ZSSH_CLOUDFLARE_CREDENTIAL_METADATA_INVENTORIED", text)
        self.assertNotIn("print(json.dumps(result, indent=2, sort_keys=True))", text)
        self.assertNotIn("set -x", text)
        self.assertNotIn("secrets.CLOUDFLARE_API_TOKEN", text)

        marker_start = text.index("ZSSH_CLOUDFLARE_CREDENTIAL_METADATA_INVENTORIED")
        marker_window = text[marker_start : marker_start + 900]
        for sensitive_detail in (
            '"locations":',
            '"secret_candidates":',
            '"variable_candidates":',
            "exact_cloudflare_api_token_locations",
        ):
            self.assertNotIn(sensitive_detail, marker_window)

    def test_probe_does_not_mutate_runtime_or_cloudflare(self) -> None:
        text = self.text
        for forbidden in (
            "systemctl restart",
            "systemctl stop",
            "systemctl start",
            "sudo tee",
            "curl -X POST",
            "curl -X PUT",
            "cloudflare.com/client/v4",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
