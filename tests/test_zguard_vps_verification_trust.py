import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zguard-vps-verification.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class ZGuardVpsVerificationTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_execution_is_owner_and_same_repo_guarded(self):
        text = self.text
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("github.event.pull_request.head.repo.owner.login == 'Zennay'", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)

    def test_checkouts_are_immutable_and_credentials_disabled(self):
        text = self.text
        self.assertEqual(text.count(CHECKOUT_V6), 2)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertEqual(text.count("persist-credentials: false"), 2)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("actions: write", text)

    def test_zcloud_exact_head_and_runner_identity_precede_external_repo(self):
        text = self.text
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        user = 'test "$(id -un)" = "ubuntu"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        external = "repository: Zennay/zGuard"
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertLess(text.index(exact), text.index(guard))
        self.assertLess(text.index(user), text.index(guard))
        self.assertLess(text.index(guard), text.index(external))

    def test_existing_zguard_validation_semantics_are_preserved(self):
        text = self.text
        self.assertIn("REQUESTED_REF: ${{ github.event_name == 'workflow_dispatch' && inputs.ref || '' }}", text)
        self.assertIn('"baseline:011dd124169faa44eadad190b6079daa2f603620"', text)
        self.assertIn('"quality:3e60b2399daa1d82d9b537a17b7690225b4f4651"', text)
        self.assertIn('"quality:a9364bec5beb0e3dca64ae0ae27f917a5d6f345e"', text)
        self.assertIn("node tests/smoke.test.js", text)
        self.assertIn("bash zbrowse/scripts/validate.sh", text)
        self.assertIn("npm audit --omit=dev --audit-level=high", text)


if __name__ == "__main__":
    unittest.main()
