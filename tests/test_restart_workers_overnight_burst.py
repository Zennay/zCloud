import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-restart-workers.yml"


class RestartWorkersOvernightBurstTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_overnight_burst_window_is_bounded(self):
        self.assertIn(
            "overnight_start = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)",
            self.text,
        )
        self.assertIn(
            "overnight_end = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)",
            self.text,
        )
        self.assertIn(
            "restore_end = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)",
            self.text,
        )

    def test_burst_requests_seven_workers_without_bypassing_allocator(self):
        self.assertIn('reconcile_payload["chatgpt_count"] = 7', self.text)
        self.assertIn('reconcile_payload["chatgpt_cooldown_seconds"] = min(', self.text)
        self.assertIn(
            'reconcile_status, reconciled = api_call(',
            self.text,
        )
        self.assertIn(
            '"/api/dynamic-workers", reconcile_payload',
            self.text,
        )

    def test_first_post_burst_hour_restores_two_workers(self):
        self.assertIn(
            "elif overnight_end <= current_utc < restore_end:",
            self.text,
        )
        self.assertIn('reconcile_payload["chatgpt_count"] = 2', self.text)


if __name__ == "__main__":
    unittest.main()
