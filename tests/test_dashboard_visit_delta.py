import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardVisitDeltaTests(unittest.TestCase):
    def test_visit_delta_uses_existing_activity_feed_and_session_baseline(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

        self.assertIn("VISIT_KEY='zcloud:last-visit:v1'", app)
        self.assertIn("VISIT_SESSION_KEY='zcloud:visit-session:v1'", app)
        self.assertIn("function visitContext()", app)
        self.assertIn("function sinceLastVisitPanel(pid='')", app)
        self.assertIn("SINCE YOUR LAST VISIT", app)
        self.assertIn("Date.parse(a.ts)>since", app)
        self.assertIn("ACTIVITY_LOADED=true", app)
        self.assertNotIn("/api/visit-delta", app)

    def test_delta_is_visible_before_secondary_reporting_on_overview_and_detail(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

        overview = app[app.index("function overview(){"):app.index("\nfunction aiRunPanel", app.index("function overview(){"))]
        detail = app[app.index("function detail(p){"):app.index("\nfunction workerDetailPanel", app.index("function detail(p){"))]

        self.assertLess(overview.index("sinceLastVisitPanel()"), overview.index('class="section-title project-section-title"'))
        self.assertLess(detail.index("sinceLastVisitPanel(p.id)"), detail.index('class="detail-summary"'))

    def test_updated_asset_revision_and_javascript_syntax(self):
        index = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/app.js?v=r5", index)

        result = subprocess.run(
            ["node", "--check", str(ROOT / "public" / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
