import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class DashboardResilienceTests(unittest.TestCase):
    def test_frontend_keeps_last_good_snapshot_and_allows_slow_status(self):
        text = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("STATUS_TIMEOUT_MS=30000", text)
        self.assertIn("zcloud:last-status:v1", text)
        self.assertIn("loadCachedStatus()", text)
        self.assertIn("saveCachedStatus(DATA)", text)
        self.assertIn("api('/api/status',STATUS_TIMEOUT_MS)", text)
        self.assertIn("Showing the last successful dashboard snapshot", text)

    def test_dashboard_recovery_validates_status_api_not_only_static_html(self):
        text = (ROOT / ".github" / "workflows" / "zcloud-dashboard-access-recovery.yml").read_text(encoding="utf-8")
        self.assertIn('status_url="http://127.0.0.1:${port}/api/status"', text)
        self.assertIn("ZCLOUD_DASHBOARD_STATUS_GREEN", text)
        self.assertIn("http://${public_ip}:${port}/api/status", text)
        self.assertIn("http://198.244.191.182:8765/api/status", text)
        self.assertIn("ZCLOUD_DASHBOARD_EXTERNAL_STATUS_GREEN", text)

if __name__ == "__main__":
    unittest.main()
