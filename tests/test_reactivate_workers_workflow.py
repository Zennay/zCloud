import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/reactivate-workers-now.yml")


class ReactivateWorkersWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_never_runs_live_reactivation(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("reactivate:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_reactivation_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_failure_diagnostics_do_not_dump_journals_or_full_process_argv(self):
        self.assertNotIn("journalctl", self.text)
        self.assertNotIn("pgrep -af", self.text)
        self.assertIn("systemctl --user show chatgpt-firefox.service", self.text)
        self.assertIn("systemctl show zennay-cloud.service", self.text)
        self.assertIn("ss -ltn ", self.text)

    def test_api_evidence_is_bounded(self):
        self.assertIn("def settings_summary(settings):", self.text)
        self.assertNotIn("json.dumps(settings,sort_keys=True)", self.text)
        self.assertNotIn('"allocation":allocation', self.text)
        self.assertNotIn('"dynamic_workers":final.get("dynamic_workers")', self.text)
        self.assertNotIn('f"start rejected for {key}: {result}"', self.text)
        self.assertIn('"command_id": int(result.get("command_id") or 0)', self.text)
        self.assertIn("ZCLOUD_WORKER_REACTIVATION_GREEN=1", self.text)

    def test_existing_recovery_scope_is_preserved(self):
        self.assertIn("systemctl --user restart chatgpt-firefox.service", self.text)
        self.assertIn("systemctl restart zennay-cloud.service", self.text)
        self.assertIn('"/api/dynamic-workers"', self.text)
        self.assertIn('"action": "start"', self.text)


if __name__ == "__main__":
    unittest.main()
