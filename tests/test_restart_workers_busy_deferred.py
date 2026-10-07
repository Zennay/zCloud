from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersBusyDeferredTests(unittest.TestCase):
    def test_busy_pending_restart_is_deferred_not_failed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('live_status, live_payload = api_call("GET", "/api/runner-live")', text)
        self.assertIn('state.get("generating")', text)
        self.assertIn('state.get("sending")', text)
        self.assertIn('"new-chat-deferred-busy"', text)
        self.assertIn('age <= 90', text)
        self.assertIn('"deferred_busy_workers": deferred_busy_workers', text)
        self.assertIn('pending_workers = hard_pending_workers', text)

    def test_idle_pending_restart_remains_hard_failure(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('hard_pending_workers.append(worker)', text)
        self.assertIn('raise SystemExit("restart commands remained pending: " + ", ".join(pending_workers))', text)


if __name__ == "__main__":
    unittest.main()
