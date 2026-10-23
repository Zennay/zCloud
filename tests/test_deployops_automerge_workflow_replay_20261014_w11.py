"""Prevent automatic integration from replaying a privileged workflow run.

Hosted source assertions only; this file never calls GitHub or a VPS.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-autonomous-safe-pr-integration.yml"
ENGINE = ROOT / "scripts/zcloud_autonomous_pr_integrator.py"


class AutoIntegrationReplayWorkflowTests(unittest.TestCase):
    def test_dispatch_identity_is_checked_before_any_checkout(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        gate = text.index("Refuse replay and untrusted dispatch before checkout or GitHub API")
        checkout = text.index("uses: actions/checkout@")
        self.assertLess(gate, checkout)
        guard = text[gate:checkout]
        for line in (
            'test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
            'test "$GITHUB_REF" = "refs/heads/main"',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_ACTOR" = "Zennay"',
            'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
            'schedule|workflow_dispatch)',
        ):
            with self.subTest(line=line):
                self.assertIn(line, guard)

    def test_immutable_checkout_and_fresh_main_before_repository_tests(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("persist-credentials: false", text)
        source = text.index("Require exact checkout and current canonical main before testing")
        regression = text.index("Validate engine and safety gates")
        self.assertLess(source, regression)
        preflight = text[source:regression]
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', preflight)
        self.assertIn('git ls-remote --exit-code origin refs/heads/main', preflight)
        self.assertIn('test "$main_sha" = "$GITHUB_SHA"', preflight)

    def test_no_new_self_hosted_or_pr_mutation_entrypoint(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  schedule:", text)
        self.assertIn("  workflow_dispatch:", text)
        self.assertNotIn("  pull_request:", text)
        self.assertNotIn("workflow_run:", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("runs-on: [self-hosted", text)
        self.assertIn("group: zcloud-autonomous-integration-main", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn(" --apply", text)

    def test_runtime_refuses_unknown_run_attempt(self):
        source = ENGINE.read_text(encoding="utf-8")
        self.assertIn('env.get("GITHUB_RUN_ATTEMPT") != "1"', source)
        self.assertIn('raise Refused("replayed_or_unknown_attempt")', source)
        self.assertLess(source.index('raise Refused("replayed_or_unknown_attempt")'),
                        source.index('branch = transport(token, "GET", "/branches/main")'))


if __name__ == "__main__":
    unittest.main()
