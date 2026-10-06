import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-resource-governor-canary.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class ResourceGovernorCanaryTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_canary_is_owner_guarded(self):
        text = self.text
        self.assertIn("if: github.actor == 'Zennay' && github.triggering_actor == 'Zennay'", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("actions: write", text)

    def test_checkout_is_immutable_exact_and_credentials_disabled(self):
        text = self.text
        self.assertEqual(text.count(CHECKOUT_V6), 1)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertIn("ref: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}", text)
        self.assertIn("fetch-depth: 1", text)
        self.assertIn("clean: true", text)
        self.assertIn("persist-credentials: false", text)

    def test_exact_head_and_runtime_user_precede_runner_guard_and_canary(self):
        text = self.text
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        user = 'test "$(id -un)" = "ubuntu"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        canary = "Prove live resource admission and process bounds"
        self.assertIn("EXPECTED_ZCLOUD_SHA: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}", text)
        self.assertLess(text.index(exact), text.index(guard))
        self.assertLess(text.index(user), text.index(guard))
        self.assertLess(text.index(guard), text.index(canary))

    def test_canary_remains_ephemeral_and_does_not_target_production_db(self):
        text = self.text
        self.assertIn('workdir="$(mktemp -d "$RUNNER_TEMP/zcloud-resource-canary.XXXXXX")"', text)
        self.assertIn('db="$workdir/canary.db"', text)
        self.assertIn("trap cleanup EXIT", text)
        self.assertNotIn("/home/ubuntu/zennay-cloud/history.db", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl", text)


if __name__ == "__main__":
    unittest.main()
