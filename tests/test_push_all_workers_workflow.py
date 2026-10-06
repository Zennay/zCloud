import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "push-all-workers-now.yml"


class PushAllWorkersWorkflowTests(unittest.TestCase):
    def test_push_all_only_supersedes_action_specific_stale_commands(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ORDINARY_COMMAND_STALE_SECONDS = 300", text)
        self.assertIn("LONG_COMMAND_STALE_SECONDS = 900", text)
        self.assertIn('"SELECT id,action,created_at FROM runner_commands "', text)
        self.assertIn('if action in {"new_chat", "drain"}', text)
        self.assertIn("if age < stale_after:", text)
        self.assertIn("PRESERVED_PENDING=", text)
        self.assertNotIn('"SELECT id,action FROM runner_commands "', text)

    def test_push_all_still_queues_every_allocated_worker(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('call("POST", "/api/dynamic-workers/force-push", {})', text)
        self.assertIn("ALL_WORKERS_PUSHED_GREEN=1", text)
        self.assertIn("failed = [w for w, v in final.items()", text)


if __name__ == "__main__":
    unittest.main()
