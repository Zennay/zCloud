"""Contract tests for the sanitized worker-consumer diagnostic workflow."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "debug-worker-consumer-now.yml"


class DebugWorkerConsumerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_never_uses_self_hosted_runner(self):
        text = self.text
        validate = text.split("  validate:", 1)[1].split("\n  debug:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)

    def test_live_debug_is_not_executed_for_pull_requests(self):
        debug = self.text.split("\n  debug:", 1)[1]
        self.assertIn("if: github.event_name != 'pull_request'", debug)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", debug)

    def test_checkout_is_immutable_exact_and_credential_free(self):
        self.assertGreaterEqual(
            self.text.count(
                "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
            ),
            2,
        )
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        self.assertGreaterEqual(self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'), 2)
        self.assertNotIn("actions/checkout@v4", self.text)

    def test_live_reads_require_canonical_runner_guard(self):
        debug = self.text.split("\n  debug:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        capture = "- name: Capture sanitized live worker consumer state"
        self.assertIn('test "$(id -un)" = "ubuntu"', debug)
        self.assertIn(guard, debug)
        self.assertIn(capture, debug)
        self.assertLess(debug.index(guard), debug.index(capture))

    def test_live_database_is_opened_strictly_read_only(self):
        self.assertIn('f"file:{DB}?mode=ro"', self.text)
        self.assertIn('uri=True', self.text)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', self.text)
        self.assertIn("DB.is_symlink()", self.text)
        self.assertNotIn('sqlite3.connect("/home/ubuntu/zennay-cloud/history.db"', self.text)

    def test_logs_do_not_emit_raw_worker_or_command_payloads(self):
        forbidden = (
            'print("WORKER_DEBUG="',
            'print("STATUS="',
            'print("RECENT_EVENTS="',
            'print("RECENT_COMMANDS="',
            "project_id,worker_slot,target,reason,error",
            "created_at,updated_at,result",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)
        self.assertIn('print("WORKER_CONSUMER_SUMMARY="', self.text)
        self.assertIn('print("WORKER_CONSUMER_DB_SUMMARY="', self.text)

    def test_worker_debug_is_reduced_to_bounded_state_counts(self):
        self.assertIn('worker_debug = get("/api/worker-debug")', self.text)
        self.assertIn('"worker_count": len(workers)', self.text)
        self.assertIn('"worker_states": dict(sorted(state_counts.items()))', self.text)
        self.assertIn('state if state in ALLOWED_WORKER_STATES else "other"', self.text)


if __name__ == "__main__":
    unittest.main()
