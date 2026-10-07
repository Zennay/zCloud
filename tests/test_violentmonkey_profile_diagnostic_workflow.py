import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-violentmonkey-profile-diagnostic.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class ViolentmonkeyProfileDiagnosticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_exact_head_and_non_mutating(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, synchronize, reopened]", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn(CHECKOUT_V6, self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn(
            "python3 -m unittest -v tests.test_violentmonkey_profile_diagnostic_workflow",
            self.text,
        )
        validate = self.text[
            self.text.index("  validate:") : self.text.index("\n  inspect:")
        ]
        for forbidden in (
            "systemctl ",
            "pgrep ",
            "/proc/",
            "sqlite3.connect",
            "chatgpt-firefox.service",
        ):
            self.assertNotIn(forbidden, validate)

    def test_live_inspection_is_permanent_runner_guarded_before_mutation(self):
        self.assertIn(
            "inspect:\n    if: github.event_name != 'pull_request' && github.repository == 'Zennay/zCloud' && github.ref == 'refs/heads/main'",
            self.text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: self-hosted\n", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(self.text.count(CHECKOUT_V6), 2)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        first_mutation = "systemctl --user stop chatgpt-firefox.service"
        self.assertIn(exact, self.text)
        self.assertIn(guard, self.text)
        self.assertIn(first_mutation, self.text)
        inspect_start = self.text.index("  inspect:")
        self.assertLess(self.text.index(exact, inspect_start), self.text.index(guard, inspect_start))
        self.assertLess(self.text.index(guard, inspect_start), self.text.index(first_mutation, inspect_start))

    def test_diagnostic_logs_do_not_emit_cmdlines_or_userscript_snippets(self):
        self.assertIn('"main_firefox_count": len(main_pids)', self.text)
        self.assertNotIn('"cmdline": cmd', self.text)
        self.assertNotIn('"snippets": snippets', self.text)
        self.assertNotIn("snippets.append(", self.text)
        self.assertIn("?mode=ro", self.text)
        self.assertIn("uri=True", self.text)
        self.assertIn("con.close()", self.text)
        self.assertIn("ZCLOUD_VIOLENTMONKEY_PROFILE_DIAGNOSTIC_GREEN=1", self.text)

    def test_pr_checks_cannot_cancel_live_diagnostic(self):
        self.assertIn(
            "group: zcloud-violentmonkey-profile-diagnostic-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || 'live' }}",
            self.text,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.text,
        )
        self.assertNotIn("cancel-in-progress: true", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
