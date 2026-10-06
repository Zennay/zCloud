import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-pr-security-gate.yml"


class PrSecurityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_pull_request_only_and_read_only(self):
        self.assertIn("pull_request:", self.text)
        self.assertNotIn("push:", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_external_actions_are_immutable_sha_pinned(self):
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, flags=re.MULTILINE)
        self.assertEqual(len(uses), 2)
        for target in uses:
            self.assertRegex(target, r"^[^@]+@[0-9a-f]{40}$")
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            uses,
        )
        self.assertIn(
            "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294",
            uses,
        )

    def test_checkout_is_exact_head_without_persisted_credentials(self):
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.text)
        self.assertIn("fetch-depth: 0", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_gate_runs_focused_tests_scanner_and_dependency_review(self):
        self.assertIn("python3 -m unittest -v tests.test_pr_security_scan", self.text)
        self.assertIn("python3 scripts/zcloud_pr_security_scan.py", self.text)
        self.assertIn("--base-ref \"${{ github.event.pull_request.base.sha }}\"", self.text)
        self.assertIn("fail-on-severity: moderate", self.text)


if __name__ == "__main__":
    unittest.main()
