import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-extension-fallback-canary.yml"
CHECKOUT_V6 = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"


class ExtensionFallbackCanaryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only_and_non_mutating(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, synchronize, reopened]", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("github.event.pull_request.head.sha", self.text)
        self.assertIn(CHECKOUT_V6, self.text)
        self.assertIn("persist-credentials: false", self.text)
        validate = self.text[
            self.text.index("  validate:") : self.text.index("\n  canary:")
        ]
        for forbidden in (
            "systemctl ",
            "rsync ",
            "/home/ubuntu/zennay-cloud",
            "/api/runner-control",
        ):
            self.assertNotIn(forbidden, validate)

    def test_live_canary_is_exact_head_permanent_runner_guarded(self):
        self.assertIn("canary:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(self.text.count(CHECKOUT_V6), 2)
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        first_runtime_write = "rsync -a --delete --exclude="
        canary_start = self.text.index("  canary:")
        self.assertLess(
            self.text.index(exact, canary_start),
            self.text.index(guard, canary_start),
        )
        self.assertLess(
            self.text.index(guard, canary_start),
            self.text.index(first_runtime_write, canary_start),
        )

    def test_live_mutations_share_one_concurrency_slot_but_pr_validation_does_not(self):
        self.assertIn(
            "group: zcloud-browser-consumer-repair-${{ github.event_name == 'pull_request' && github.ref || 'live' }}",
            self.text,
        )
        self.assertIn("cancel-in-progress: false", self.text)

    def test_snapshot_and_failure_rollback_cover_every_overwritten_runtime_file(self):
        self.assertIn('cp -a "$service_unit" "$backup/chatgpt-firefox.service"', self.text)
        self.assertIn(
            'cp -a "$prestart" "$backup/chatgpt-firefox-prestart.sh"', self.text
        )
        self.assertIn('echo "was_active=$active" >> "$GITHUB_OUTPUT"', self.text)
        self.assertIn("if: failure() && steps.snapshot.outcome == 'success'", self.text)
        self.assertIn("HAD_SERVICE: ${{ steps.snapshot.outputs.had_service }}", self.text)
        self.assertIn("HAD_PRESTART: ${{ steps.snapshot.outputs.had_prestart }}", self.text)
        self.assertIn("WAS_ACTIVE: ${{ steps.snapshot.outputs.was_active }}", self.text)
        self.assertIn(
            'install -D -m 0644 "$BACKUP/chatgpt-firefox.service" "$service_unit"',
            self.text,
        )
        self.assertIn(
            'install -D -m 0755 "$BACKUP/chatgpt-firefox-prestart.sh" "$prestart"',
            self.text,
        )
        self.assertIn('if [[ "$WAS_ACTIVE" == "active" ]]', self.text)
        self.assertIn("EXTENSION_FALLBACK_ROLLED_BACK=1", self.text)

    def test_database_reads_are_explicitly_read_only(self):
        self.assertGreaterEqual(self.text.count("?mode=ro"), 2)
        self.assertGreaterEqual(self.text.count("uri=True"), 2)
        self.assertNotIn("INSERT INTO", self.text)
        self.assertNotIn("UPDATE runner_", self.text)
        self.assertNotIn("DELETE FROM", self.text)

    def test_canary_evidence_omits_prompt_and_raw_error_payloads(self):
        self.assertIn('"prompt_sha256": hashlib.sha256', self.text)
        self.assertIn('"prompt_length": len(', self.text)
        self.assertNotIn('"prompt": cfg.get("prompt")', self.text)
        self.assertNotIn("SELECT id,status,result,updated_at", self.text)
        self.assertNotIn("SELECT ts,event,reason,error", self.text)
        self.assertIn("SELECT id,status,updated_at", self.text)
        self.assertIn("SELECT ts,event FROM runner_events", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
