"""Contract tests for the zSSH public-gateway preflight workflow."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-public-gateway-preflight.yml"


class ZsshPublicGatewayPreflightWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_path_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  prove:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)

    def test_live_proof_never_runs_for_pull_requests(self):
        prove = self.text.split("\n  prove:", 1)[1]
        self.assertIn("if: github.event_name != 'pull_request'", prove)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", prove)

    def test_push_is_main_only(self):
        self.assertIn("  push:\n    branches: [main]", self.text)

    def test_all_checkouts_are_immutable_and_credential_free(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(3, self.text.count(pinned))
        self.assertEqual(3, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)

    def test_live_proof_checks_exact_zcloud_revision_and_runner_before_external_code(self):
        prove = self.text.split("\n  prove:", 1)[1]
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        external_checkout = "repository: Zennay/zSSH"
        self.assertIn(exact, prove)
        self.assertIn('test "$(id -un)" = "ubuntu"', prove)
        self.assertIn(guard, prove)
        self.assertLess(prove.index(guard), prove.index(external_checkout))

    def test_external_release_is_exact_and_verified(self):
        self.assertIn(
            "EXPECTED_ZSSH_SHA: d2a0b2259deac4b6c7363258dc85e7b213ae507c",
            self.text,
        )
        self.assertIn("ref: ${{ env.EXPECTED_ZSSH_SHA }}", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZSSH_SHA"', self.text)
        self.assertIn('ZSSH_EXPECTED_SHA="$EXPECTED_ZSSH_SHA"', self.text)

    def test_preflight_remains_validate_only(self):
        self.assertIn("ZSSH_PUBLIC_GATEWAY_VALIDATE_ONLY=1", self.text)
        self.assertIn('test ! -e "$HOME/.config/zssh/public-gateway.env"', self.text)
        self.assertIn('test ! -e "$HOME/.local/share/zssh-public/current"', self.text)

    def test_raw_review_target_report_is_not_printed(self):
        self.assertNotIn('printf \'%s\\n\' "$output"', self.text)
        self.assertIn("console.log(JSON.stringify({", self.text)
        self.assertIn("trusted_key_count: trusted.size", self.text)


if __name__ == "__main__":
    unittest.main()
