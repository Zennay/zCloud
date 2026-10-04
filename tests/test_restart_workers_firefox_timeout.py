import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersFirefoxTimeoutTests(unittest.TestCase):
    def test_restart_timeout_verifies_live_firefox_before_failing(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('live_status, live = api_call("GET", "/api/runner-live")', text)
        self.assertIn('candidate.get("active")', text)
        self.assertIn('"initial_restart": restart_initial', text)
        self.assertIn("time.time() + 35", text)

    def test_runtime_verification_stays_fail_closed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if not firefox_runtime:", text)
        self.assertIn("Firefox worker consumer restart failed", text)


if __name__ == "__main__":
    unittest.main()
