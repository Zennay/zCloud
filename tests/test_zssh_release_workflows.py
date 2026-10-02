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


if __name__ == "__main__":
    unittest.main()
