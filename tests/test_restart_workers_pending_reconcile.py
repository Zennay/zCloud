import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersPendingReconcileTests(unittest.TestCase):
    def test_pending_commands_get_one_bounded_health_backed_continuation(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        normal = text.index("final = poll_exact_command_states(35)")
        reconcile = text.index("if pending_command_ids:")
        extended = text.index("final = poll_exact_command_states(45)")
        self.assertLess(normal, reconcile)
        self.assertLess(reconcile, extended)
        self.assertIn("health_deadline = time.time() + 20", text)
        self.assertIn(
            'live_status, live = api_call("GET", "/api/runner-live")',
            text,
        )
        self.assertIn('candidate.get("active")', text)
        self.assertIn('"consumer_healthy": consumer_healthy', text)

    def test_continuation_polls_exact_command_ids_not_worker_wide_state(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("def read_exact_command_states():", text)
        self.assertIn(
            '"SELECT status,result,updated_at FROM runner_commands WHERE id=?"',
            text,
        )
        self.assertIn('(item["command_id"],)', text)
        self.assertIn(
            'int(item["command_id"])',
            text,
        )
        self.assertIn('"pending_command_ids": pending_command_ids', text)

    def test_unhealthy_consumer_still_fails_closed(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("if not consumer_healthy:", text)
        self.assertIn(
            "failed bounded health reconciliation",
            text,
        )
        self.assertIn("timeout-minutes: 4", text)

    def test_standalone_violentmonkey_mode_uses_exact_new_pid_health(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('health_source = "standalone-firefox"', text)
        self.assertIn("os.kill(new_pid, 0)", text)
        self.assertIn("except (ProcessLookupError, TypeError):", text)


if __name__ == "__main__":
    unittest.main()
