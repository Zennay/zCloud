"""Trust and reachability checks for the hosted mobile worker-console CI.

This test only reads workflow source and never contacts or modifies the VPS.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-mobile-worker-console-hosted.yml"


class MobileConsoleHostedCITrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_legacy_branch_only_trigger_is_fixed(self):
        self.assertIn("  pull_request:\n    branches: [main]", self.text)
        self.assertIn("  push:\n    branches: [main, fix/ui-consistency-tokens-and-mobile-20261010]", self.text)
        self.assertIn("  workflow_dispatch:", self.text)
        for path in ("public/app.js", "public/index.html", "public/enhancements.css",
                     "public/enhancements.js", "tests/test_worker_debug_panel.js",
                     "tests/test_mobile_css_delivery_20261010.py",
                     "tests/test_dashboard_design_system.py",
                     "public/design-system.css", "tests/test_global_worker_console_ui.py",
                     "tests/test_mobile_worker_console_navigation.py",
                     "tests/test_mobile_worker_actions_20261010.py",
                     "tests/test_zcloud_mobile_console_ci_trust_20261014.py"):
            self.assertGreaterEqual(self.text.count("- " + path), 2, path)

    def test_canonical_same_repo_pr_and_only_hosted_read_access(self):
        self.assertIn("github.repository == 'Zennay/zCloud'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        for forbidden in ("self-hosted", "contents: write", "pull-requests: write",
                          "pull_request_target:", "secrets.", "systemctl ", "sudo ",
                          "gh workflow run", "deploy/", "zcloud/vps-production"):
            self.assertNotIn(forbidden, self.text)

    def test_immutable_node24_and_exact_sha_check_precedes_code(self):
        checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        node = "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
        self.assertEqual(self.text.count(checkout), 1)
        self.assertEqual(self.text.count(node), 1)
        self.assertNotRegex(self.text, r"actions/(?:checkout|setup-node)@v[0-9]")
        self.assertIn("node-version: '24'", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("clean: true", self.text)
        self.assertIn("fetch-depth: 1", self.text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.text)
        self.assertIn('test "$EXPECTED_REPO" = "Zennay/zCloud"', self.text)
        self.assertLess(self.text.index('test "$(git rev-parse HEAD)"'), self.text.index("Check dashboard JavaScript and worker diagnostics behavior"))
        # No repository-controlled tests execute before exact source verification.
        window = self.text[self.text.index("Checkout exact immutable source"):self.text.index("Check dashboard JavaScript and worker diagnostics behavior")]
        self.assertNotRegex(window, r"(?m)^\s*run: (?:node|python|bash)")

    def test_real_worker_debug_and_enhancement_js_are_executed(self):
        self.assertIn("node --check public/enhancements.js", self.text)
        self.assertIn("node tests/test_worker_debug_panel.js", self.text)
        self.assertIn("node --check public/app.js", self.text)
        self.assertLess(
            self.text.index("Verify canonical repository and exact head before running code"),
            self.text.index("Check dashboard JavaScript and worker diagnostics behavior"),
        )
        self.assertLess(
            self.text.index("node tests/test_worker_debug_panel.js"),
            self.text.index("Run all repository Python regression tests"),
        )

    def test_preserves_mobile_and_full_regression_coverage(self):
        for command in ("node --check public/app.js",
                        "tests.test_global_worker_console_ui",
                        "tests.test_mobile_worker_console_navigation",
                        "tests.test_mobile_worker_actions_20261010",
                        "python3 -m unittest discover -v"):
            self.assertIn(command, self.text)


if __name__ == "__main__":
    unittest.main()
