import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FirefoxControlPlanePriorityTests(unittest.TestCase):
    def test_firefox_is_not_niced_below_competing_compute(self):
        text = (ROOT / "deploy" / "chatgpt-firefox.service").read_text()
        self.assertIn("Nice=0", text)
        self.assertNotIn("Nice=10", text)

    def test_firefox_keeps_default_cpu_weight_without_hard_quota(self):
        text = (ROOT / "deploy" / "chatgpt-firefox.service").read_text()
        self.assertIn("CPUWeight=100", text)
        self.assertNotIn("CPUQuota=", text)
        self.assertIn("Restart=always", text)
        self.assertIn("RestartSec=5", text)


if __name__ == "__main__":
    unittest.main()
