"""Keep the Worker-v2 consent proof itself hosted-only and immutable."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github/workflows/zcloud-v2-legacy-consent-hosted-20261014-w1.yml"


class LegacyConsentHostedCIContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CI.read_text(encoding="utf-8")

    def test_only_hosted_runners_and_no_write_token(self):
        self.assertEqual(self.text.count("runs-on: ubuntu-latest"), 2)
        for forbidden in ("self-hosted", "actions: write", "contents: write",
                          "issues: write", "pull-requests: write", "secrets.",
                          "sudo ", "gh api --method POST", "workflow_run:",
                          "schedule:", "workflow_dispatch:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_exact_sha_bound_and_credentials_not_persisted(self):
        self.assertEqual(self.text.count(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        ), 2)
        self.assertEqual(self.text.count("persist-credentials: false"), 2)
        self.assertEqual(self.text.count(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
        ), 2)
        self.assertEqual(self.text.count(
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'
        ), 2)

    def test_self_test_and_focused_manual_admission_regressions_run(self):
        self.assertIn("python3 -m unittest -v "
                      "tests.test_zcloud_v2_legacy_service_audit_trust_20261013_w1 "
                      "tests.test_zcloud_v2_legacy_consent_hosted_20261014_w1", self.text)
        for path in (".github/workflows/zcloud-v2-legacy-service-audit.yml",
                     ".github/workflows/zcloud-v2-legacy-consent-hosted-20261014-w1.yml",
                     "tests/test_zcloud_v2_legacy_service_audit_trust_20261013_w1.py",
                     "tests/test_zcloud_v2_legacy_consent_hosted_20261014_w1.py"):
            self.assertEqual(self.text.count("- " + path), 2)
        self.assertIn("python3 -m unittest discover -v", self.text)

    def test_only_branch_push_and_same_repo_pr(self):
        self.assertIn("fix/deployops-v2-legacy-manual-replay-gate-20261014-w1", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertEqual(self.text.count("github.event.pull_request.head.repo.full_name == github.repository"), 2)
        self.assertNotIn("cancel-in-progress: true", self.text)


if __name__ == "__main__":
    unittest.main()
