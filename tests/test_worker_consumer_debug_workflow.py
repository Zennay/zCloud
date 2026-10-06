import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/debug-worker-consumer-now.yml")


class WorkerConsumerDebugWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_validation_never_reads_live_state(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("debug:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_debug_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_history_db_is_strictly_read_only(self):
        self.assertIn('"?mode=ro"', self.text)
        self.assertIn("sqlite3.connect(uri, uri=True", self.text)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', self.text)
        self.assertIn("st_mtime_ns", self.text)

    def test_raw_sensitive_fields_are_not_selected_or_dumped(self):
        self.assertNotIn("WORKER_DEBUG=", self.text)
        self.assertNotIn("RECENT_EVENTS=", self.text)
        self.assertNotIn("RECENT_COMMANDS=", self.text)
        self.assertNotIn("target,reason,error", self.text)
        self.assertNotIn("status,result,updated_at", self.text)
        self.assertIn("WORKER_DEBUG_SUMMARY=", self.text)
        self.assertIn("RECENT_EVENT_METADATA=", self.text)
        self.assertIn("RECENT_COMMAND_METADATA=", self.text)

    def test_diagnostic_has_terminal_read_only_marker(self):
        self.assertIn("ZCLOUD_WORKER_CONSUMER_DEBUG_READONLY_GREEN=1", self.text)
        self.assertNotIn("sudo ", self.text)
        self.assertNotIn("systemctl restart", self.text)


if __name__ == "__main__":
    unittest.main()
