import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-regression-smoke.yml"


class RegressionWorkflowSupplyChainTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_actions_are_immutable_and_node24_native(self):
        text = self.text()
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
            text,
        )
        self.assertIn("node-version: 24", text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertNotIn("actions/setup-node@v4", text)
        self.assertNotIn("node-version: 20", text)

    def test_pr_regression_checks_exact_head_read_only(self):
        text = self.text()
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("clean: true", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull_request_target:", text)\n
    def test_regression_suite_contract_remains_intact(self):
        text = self.text()
        required = (
            "python3 -m unittest discover -v",
            "bash tests/test_birds_eye_review.sh",
            "bash tests/test_improvement_stopgate_js.sh",
            "bash tests/test_autonomy_runner_js.sh",
            "node tests/test_reload_extension.mjs",
            "node tests/test_firefox_recovery.js",
            "node tests/test_firefox_recovery_contract.js",
            "python3 -m unittest -v tests.test_prechange_guard",
            "python3 -m unittest -v tests.test_postdeploy_canary",
            "python3 -m unittest -v tests.test_transactional_promote",
            "python3 -m unittest -v tests.test_zcloud_healthcheck",
            "python3 -m unittest -v tests.test_config_validation",
        )
        for command in required:
            with self.subTest(command=command):
                self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
