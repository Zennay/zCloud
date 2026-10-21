import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/recover-pending-workers.yml")


class RecoverPendingWorkersWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_validation_does_not_run_recovery(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("recover:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_recovery_is_exact_head_and_permanent_runner_guarded(self):
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

    def test_recovery_payload_logging_is_bounded(self):
        self.assertIn("def command_summary(payload):", self.text)
        self.assertIn('"command_id": int(payload.get("command_id") or 0)', self.text)
        self.assertNotIn('"projects":targets.get("projects")', self.text)
        self.assertNotIn('"start":start', self.text)
        self.assertNotIn('"new_chat":newchat', self.text)
        self.assertNotIn("TARGETS_FINAL", self.text)
        self.assertIn("RECOVERY_FINAL_SUMMARY=", self.text)

    def test_mutation_scope_remains_worker_control_only(self):
        self.assertIn('"action": "start"', self.text)
        self.assertIn('"action": "new_chat"', self.text)
        self.assertNotIn("/api/dynamic-workers", self.text)
        self.assertNotIn("systemctl restart", self.text)
        self.assertIn("ZCLOUD_PENDING_WORKER_RECOVERY_GREEN=1", self.text)


if __name__ == "__main__":
    unittest.main()
