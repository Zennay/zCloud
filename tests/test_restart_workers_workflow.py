import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersWorkflowTests(unittest.TestCase):
    def test_restart_uses_reconciled_live_allocation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('api_call("POST", "/api/dynamic-workers", reconcile_payload)', text)
        self.assertIn('api_call("GET", "/api/runner-targets")', text)
        self.assertIn('get("global_allocation")', text)
        self.assertNotIn(
            '"SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot"',
            text,
        )

    def test_restart_still_supersedes_stale_commands_and_opens_fresh_chats(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("restart workflow superseded stale pending command", text)
        self.assertIn('(key, "new_chat", "pending", ts, ts)', text)
        self.assertIn("restart commands remained pending", text)


if __name__ == "__main__":
    unittest.main()
