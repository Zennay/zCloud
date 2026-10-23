"""Guard the Node 24, exact-head, read-only GitHub-hosted regression lane.

This source-only contract deliberately does not authorize production or self-hosted work.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1]
            / ".github/workflows/zcloud-regression-smoke.yml")


class RegressionNode24RestackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_action_dependencies_are_immutable_and_use_node24(self):
        expected = {
            "actions/checkout": "d23441a48e516b6c34aea4fa41551a30e30af803",
            "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",
        }
        actual = dict(re.findall(r"(?m)^\s*- uses: (actions/(?:checkout|setup-node))@([a-z0-9]+)", self.workflow))
        self.assertEqual(actual, expected)
        self.assertIn("node-version: 24", self.workflow)
        self.assertNotIn("node-version: 20", self.workflow)

    def test_exact_pr_head_never_persists_credentials(self):
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("clean: true", self.workflow)

    def test_exact_checkout_is_verified_before_any_repository_test(self):
        text = self.workflow
        gate = "Verify exact immutable revision before executing repository code"
        self.assertIn(gate, text)
        self.assertEqual(text.count(gate), 1)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", text)
        self.assertIn("EXPECTED_REPOSITORY: ${{ github.repository }}", text)
        self.assertIn('test "$EXPECTED_REPOSITORY" = "Zennay/zCloud"', text)
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("set -euo pipefail", text)
        self.assertLess(text.index(gate), text.index("Run critical zCloud smoke suite"))
        # No repo-controlled code may run between checkout and identity verification.
        window = text[text.index("actions/setup-node@"):text.index(gate)]
        self.assertNotIn("run: ", window)

    def test_only_readonly_hosted_source_checks(self):
        self.assertIn("permissions:\n  contents: read\n", self.workflow)
        self.assertIn("runs-on: ubuntu-latest", self.workflow)
        for prohibited in ("contents: write", "pull-requests: write",
                           "pull_request_target:", "runs-on: [self-hosted",
                           "systemctl ", "gh workflow run"):
            self.assertNotIn(prohibited, self.workflow)

    def test_current_main_smoke_coverage_is_preserved(self):
        commands = (
            "python3 -m unittest discover -v",
            "bash tests/test_birds_eye_review.sh",
            "bash tests/test_improvement_stopgate_js.sh",
            "bash tests/test_autonomy_runner_js.sh",
            "node tests/test_reload_extension.mjs",
            "node tests/test_firefox_recovery.js",
            "node tests/test_firefox_recovery_contract.js",
            "node tests/test_worker_tab_reuse.js",
            "node tests/test_worker_tab_identity_guard.js",
            "node tests/test_violentmonkey_send_tab_preservation.js",
            "python3 -m unittest -v tests.test_prechange_guard",
            "python3 -m unittest -v tests.test_postdeploy_canary",
            "python3 -m unittest -v tests.test_transactional_promote",
            "python3 -m unittest -v tests.test_zcloud_healthcheck",
            "python3 -m unittest -v tests.test_config_validation",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)


if __name__ == "__main__":
    unittest.main()
