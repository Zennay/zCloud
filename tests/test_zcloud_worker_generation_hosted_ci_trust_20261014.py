"""Deny-first hosted source checks for actual worker-generation evidence CI.

This contract has no runtime/VPS or production authority.
"""
from pathlib import Path
import re
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/zcloud-worker-generation-evidence-hosted-20261013-w1.yml"
)


class WorkerGenerationHostedSourceTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_retains_evidence_test_sources_and_new_safety_gate(self):
        for path in (
            "server.py",
            "public/zcloud-worker.user.js",
            "tests/_worker_env.py",
            "scripts/zcloud_worker_generation_evidence_20261013_w1.py",
            "tests/test_zcloud_worker_generation_evidence_20261013_w1.py",
            "tests/test_zcloud_worker_generation_http_integration_20261013_w1.py",
            "ops/zssh-runner-recipes/zcloud-generation-check.py",
            "docs/control-plane-generation-evidence-20261013-w1.md",
            "tests/test_zcloud_worker_generation_hosted_ci_trust_20261014.py",
        ):
            with self.subTest(path=path):
                self.assertIn('      - "' + path + '"', self.workflow)

    def test_fork_pr_cannot_run_source_tests_on_this_canonical_repo(self):
        self.assertIn("github.repository == 'Zennay/zCloud'", self.workflow)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.workflow,
        )
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("runs-on: ubuntu-latest", self.workflow)
        for bad in ("pull_request_target:", "self-hosted", "contents: write",
                    "actions: write", "secrets.", "systemctl ", "sudo ",
                    "gh workflow run", "repository_dispatch:", "curl "):
            self.assertNotIn(bad, self.workflow)

    def test_immutable_checkout_and_exact_sha_check_are_before_source_execution(self):
        checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(self.workflow.count(checkout), 1)
        self.assertNotIn("actions/checkout@v4", self.workflow)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("clean: true", self.workflow)
        self.assertIn("fetch-depth: 1", self.workflow)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", self.workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.workflow)
        self.assertIn('[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', self.workflow)
        self.assertIn('test "$EXPECTED_REPO" = "Zennay/zCloud"', self.workflow)
        gate = self.workflow.index("Verify immutable source before running worker-evidence code")
        evidence_test = self.workflow.index("Verify interpreter and test real module")
        self.assertLess(gate, evidence_test)
        # The only run script before repository tests is the identity check.
        between = self.workflow[gate:evidence_test]
        self.assertEqual(len(re.findall(r"(?m)^\s*run: \|", between)), 1)
        self.assertNotIn("python3 ", between)

    def test_real_generation_classifiers_still_run_and_claim_no_live_proof(self):
        for command in (
            "python3 -m py_compile scripts/zcloud_worker_generation_evidence_20261013_w1.py",
            "tests.test_zcloud_worker_generation_evidence_20261013_w1",
            "tests.test_zcloud_worker_generation_http_integration_20261013_w1",
            "tests.test_zcloud_worker_generation_hosted_ci_trust_20261014",
            "Not proof of VPS generation, an open browser tab, or live deployment.",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)


if __name__ == "__main__":
    unittest.main()
