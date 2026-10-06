from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-auth0-reviewer-metadata-probe.yml"


class ZsshAuth0ReviewerMetadataProbeTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_probe_requires_trusted_main_activation_before_vps_execution(self) -> None:
        text = self.text
        for required in (
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.event.pull_request.merged == true",
            "github.event.pull_request.base.ref == 'main'",
            "github.event.pull_request.head.repo.full_name == github.repository",
            "runs-on: [self-hosted, zcloud, vps]",
        ):
            self.assertIn(required, text)

    def test_probe_pins_and_verifies_exact_checkout_before_metadata_access(self) -> None:
        text = self.text
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
        exact_revision = text.index('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"')
        trigger_read = text.index("cat .github/zssh-auth0-reviewer-probe-trigger")
        runner_guard = text.index("scripts/zcloud_vps_runner_guard.py --json")
        metadata_access = text.index("gh auth status --hostname github.com")
        self.assertLess(exact_revision, trigger_read)
        self.assertLess(exact_revision, runner_guard)
        self.assertLess(runner_guard, metadata_access)

    def test_metadata_logs_are_bounded_and_actions_are_immutable(self) -> None:
        text = self.text
        self.assertIn('"secret_values_read": False', text)
        self.assertIn('"mutation_attempted": False', text)
        self.assertIn("ZSSH_AUTH0_REVIEWER_METADATA_INVENTORIED", text)
        self.assertNotIn("print(json.dumps(result, indent=2, sort_keys=True))", text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertNotIn("actions/upload-artifact@v4", text)
        self.assertRegex(
            text,
            r"uses: actions/upload-artifact@[0-9a-f]{40} # v7\.0\.1",
        )
        marker = text.index("ZSSH_AUTH0_REVIEWER_METADATA_INVENTORIED")
        window = text[marker : marker + 900]
        for detail in (
            '"locations":',
            '"secret_locations":',
            '"variable_locations":',
            "AUTH0_MANAGEMENT_API_TOKEN",
            "ZSSH_REVIEW_ACCESS_TOKEN",
        ):
            self.assertNotIn(detail, window)


if __name__ == "__main__":
    unittest.main()
