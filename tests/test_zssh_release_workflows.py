from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts import zssh_release_coordination as coordination

ROOT = Path(__file__).resolve().parents[1]


class ZsshReleaseWorkflowTests(unittest.TestCase):
    def test_public_plugin_queue_stays_below_ftmo_p0(self):
        text = (ROOT / ".github/workflows/zssh-public-plugin-priority.yml").read_text(encoding="utf-8")
        self.assertIn('"priority": "P1"', text)
        self.assertIn('!= expected["priority"]', text)
        self.assertNotIn('"priority": "P0"', text)

    def test_release_coordination_uses_control_plane_health_profile(self):
        text = (ROOT / "scripts/zssh_release_coordination.py").read_text(encoding="utf-8")
        self.assertIn('"vps_profile": "control_plane"', text)
        self.assertIn('get_json(BASE + "/api/status")', text)

    def test_release_only_main_advance_allows_review_evidence_drift(self):
        self.assertTrue(coordination.release_only_main_advance([
            ".github/workflows/public-release-gate.yml",
            "scripts/check-public-release-config.mjs",
            "docs/openai-plugin-review.md",
        ]))

    def test_release_only_main_advance_rejects_runtime_drift(self):
        self.assertFalse(coordination.release_only_main_advance([
            ".github/workflows/public-release-gate.yml",
            "server.mjs",
        ]))
        self.assertFalse(coordination.release_only_main_advance([]))

    def test_vps_release_proves_public_listing_site_without_switching_live_profile(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("ZSSH_RELEASE_SHA: c1a249e4995605b025496a0178cacbc4cfcecf41", text)
        self.assertIn("Verify isolated public review site on exact release", text)
        self.assertIn("ZSSH_PUBLIC_LISTING_SITE_VPS_GREEN", text)
        self.assertIn("ZSSH_PLUGIN_PROFILE=public", text)
        self.assertIn("ZSSH_PUBLIC_AUTH_MODE=legacy", text)
        self.assertIn("Your Linux target stays yours.", text)
        self.assertIn("/support /privacy /terms", text)

    def test_public_release_finalizer_is_exact_and_success_gated(self):
        text = (ROOT / ".github/workflows/finalize-zssh-public-release-20261002.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("zssh-openai-public-plugin-release", text)
        self.assertIn("c1a249e4995605b025496a0178cacbc4cfcecf41", text)
        self.assertIn("ZCLOUD_ZSSH_PUBLIC_RELEASE_QUEUE_DONE_GREEN=1", text)
        self.assertNotIn("portfolio_queue_drop", text)

    def test_vps_release_uses_permanent_runner_guard(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        match = re.search(r"ZSSH_RELEASE_SHA:\s*([0-9a-f]{40})", text)
        self.assertIsNotNone(match)

    def test_vps_release_records_evidence_backed_zssh_receipt(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("Record evidence-backed zSSH release state receipt", text)
        self.assertIn("--project zssh", text)
        self.assertIn('--commit "$ZSSH_RELEASE_SHA"', text)
        self.assertIn("--ci-status success", text)
        self.assertIn('"live_marker": "ZSSH_STANDALONE_M1_LIVE_GREEN"', text)
        self.assertIn('"public_listing_marker": "ZSSH_PUBLIC_LISTING_SITE_VPS_GREEN"', text)
        self.assertIn('--source "github-actions:zssh-standalone-vps-release"', text)
        receipt_pos = text.index("Record evidence-backed zSSH release state receipt")
        release_pos = text.index("Release coordination claim")
        self.assertLess(receipt_pos, release_pos)

    def test_public_gateway_validate_only_proof_uses_zcloud_runner_lane(self):
        text = (ROOT / ".github/workflows/zssh-public-gateway-vps-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertNotRegex(text, r"ZSSH_SHA:\s*[0-9a-f]{40}")
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("id: zssh_target", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn('echo "ZSSH_SHA=$zssh_sha" >> "$GITHUB_ENV"', text)
        self.assertIn('ref: ${{ steps.zssh_target.outputs.sha }}', text)
        self.assertIn("ZSSH_CANONICAL_MAIN_RESOLVED", text)
        self.assertIn('test "$live_main_after" = "$ZSSH_SHA"', text)
        self.assertIn("ZSSH_CANONICAL_MAIN_RECONFIRMED", text)
        self.assertIn('test "$(git -C zssh-source rev-parse HEAD)" = "$ZSSH_SHA"', text)
        self.assertIn('bash "$source_root/deploy/prepare-reviewer-target.sh" "$source_root" > "$reviewer_report"', text)
        self.assertNotIn('npm --prefix "$source_root" run review:target', text)
        self.assertIn('ZSSH_PUBLIC_BASE_URL="https://mcp.zssh-preflight.invalid"', text)
        self.assertIn('ZSSH_PUBLIC_RATE_LIMIT_PER_MINUTE=120', text)
        self.assertIn('ZSSH_PUBLIC_RATE_LIMIT_MAX_PROFILES=10000', text)
        self.assertIn('public_rate_limit_per_minute: Number(process.env.RATE_LIMIT_PER_MINUTE)', text)
        self.assertIn('public_rate_limit_max_profiles: Number(process.env.RATE_LIMIT_MAX_PROFILES)', text)
        self.assertIn('ZSSH_PUBLIC_GATEWAY_VALIDATE_ONLY=1', text)
        self.assertIn('test "$after_env" = "$before_env"', text)
        self.assertIn('test "$after_current" = "$before_current"', text)
        self.assertIn('test "$after_active" = "$before_active"', text)
        self.assertIn('test "$after_enabled" = "$before_enabled"', text)
        self.assertIn("ZSSH_PUBLIC_GATEWAY_VPS_PREFLIGHT_GREEN", text)
        self.assertIn('--project zssh', text)
        self.assertIn('--commit "$ZSSH_SHA"', text)
        self.assertIn('--source "github-actions:zssh-public-gateway-vps-preflight"', text)


if __name__ == "__main__":
    unittest.main()
