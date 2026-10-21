import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-exact-worker-prompt-reconcile.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class ExactWorkerPromptReconcileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_never_runs_live_reconcile(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, synchronize, reopened]", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn(CHECKOUT_V6, self.text)
        self.assertIn("persist-credentials: false", self.text)
        validate = self.text[
            self.text.index("  validate:") : self.text.index("\n  reconcile:")
        ]
        for forbidden in (
            "systemctl ",
            "/home/ubuntu/zennay-cloud",
            "install -m 0644",
            "curl ",
        ):
            self.assertNotIn(forbidden, validate)

    def test_live_reconcile_is_exact_head_and_runner_guarded(self):
        self.assertIn("reconcile:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(self.text.count(CHECKOUT_V6), 2)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        first_write = 'install -m 0644 server.py "$root/server.py"'
        reconcile_start = self.text.index("  reconcile:")
        self.assertLess(self.text.index(exact, reconcile_start), self.text.index(guard, reconcile_start))
        self.assertLess(self.text.index(guard, reconcile_start), self.text.index(first_write, reconcile_start))

    def test_failed_runtime_restart_has_bounded_rollback(self):
        self.assertIn("rollback_runtime() {", self.text)
        self.assertIn("trap rollback_runtime EXIT", self.text)
        self.assertIn('install -m 0644 "$backup/server.py" "$root/server.py"', self.text)
        self.assertIn(
            'install -m 0644 "$backup/public/zcloud-worker.user.js" "$root/public/zcloud-worker.user.js"',
            self.text,
        )
        self.assertIn(
            'install -m 0644 "$backup/firefox-extension/background.js" "$root/firefox-extension/background.js"',
            self.text,
        )
        self.assertIn("ZCLOUD_PROMPT_RUNTIME_ROLLBACK_ATTEMPTED=1", self.text)
        self.assertIn("sudo -n systemctl restart zennay-cloud.service || true", self.text)

    def test_live_prompt_proof_logs_hashes_not_prompt_contents(self):
        self.assertIn("hashlib.sha256(actual.encode", self.text)
        self.assertIn('"prompt_sha256": actual_sha', self.text)
        self.assertIn('"prompt_length": len(actual)', self.text)
        self.assertNotIn('evidence.append({"worker": key, "prompt": actual})', self.text)
        self.assertNotIn("actual!r", self.text)
        self.assertNotIn("expected!r", self.text)

    def test_pr_checks_do_not_cancel_live_reconcile(self):
        self.assertIn(
            "group: zcloud-exact-worker-prompt-reconcile-${{ github.event_name }}-${{ github.ref }}",
            self.text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
