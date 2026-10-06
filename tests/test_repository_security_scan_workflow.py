import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-repository-security-scan.yml"


class RepositorySecurityScanWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_runs_for_every_main_pr_and_main_push(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("push:\n    branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("paths:", text)

    def test_scan_is_hosted_read_only_and_supply_chain_pinned(self):
        text = self.text()
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn(
            "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514",
            text,
        )
        self.assertIn("version: v0.70.0", text)
        self.assertNotIn("continue-on-error", text)

    def test_scanner_capability_is_self_tested_before_repository_scan(self):
        text = self.text()
        self.assertIn("Prove secret scanner fails closed", text)
        self.assertIn("openssl genpkey -algorithm RSA", text)
        self.assertIn("--scanners secret", text)
        self.assertIn("--exit-code 17", text)
        self.assertIn("TRIVY_SECRET_SELFTEST_GREEN", text)

        self.assertIn("Prove vulnerability scanner fails closed", text)
        self.assertIn('"lodash": "4.17.20"', text)
        self.assertIn("--scanners vuln", text)
        self.assertIn("--exit-code 23", text)
        self.assertIn("TRIVY_VULN_SELFTEST_GREEN", text)

        self.assertLess(
            text.index("TRIVY_SECRET_SELFTEST_GREEN"),
            text.index("Scan tracked repository for vulnerabilities and secrets"),
        )
        self.assertLess(
            text.index("TRIVY_VULN_SELFTEST_GREEN"),
            text.index("Scan tracked repository for vulnerabilities and secrets"),
        )

    def test_repository_findings_fail_closed(self):
        text = self.text()
        self.assertIn("--scanners vuln,secret", text)
        self.assertIn("--severity HIGH,CRITICAL", text)
        self.assertIn("--ignore-unfixed", text)
        self.assertIn("--exit-code 1", text)
        self.assertIn("--format table", text)
        self.assertIn("timeout-minutes: 10", text)


if __name__ == "__main__":
    unittest.main()
