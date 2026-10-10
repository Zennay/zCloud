import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardUserFirstHierarchyTests(unittest.TestCase):
    def test_overview_prioritizes_project_actions_before_reporting_and_diagnostics(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        start = app.index("function overview(){")
        end = app.index("\nfunction aiRunPanel", start)
        overview = app[start:end]

        self.assertIn("function globalWorkerConsole()", app)
        self.assertLess(overview.index("globalWorkerConsole()"), overview.index("attentionPanel()"))
        self.assertLess(overview.index("globalWorkerConsole()"), overview.index('class="project-grid"'))
        self.assertLess(overview.index('class="project-grid"'), overview.index('class="stat-grid"'))
        self.assertNotIn('data-disclosure="automation-settings"', overview)
        self.assertIn("System controls", overview)
        self.assertIn('data-disclosure="system-controls"', overview)

    def test_project_detail_keeps_primary_action_above_context_and_hides_worker_detail(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        start = app.index("function detail(p){")
        end = app.index("\nfunction workerDetailPanel", start)
        detail = app[start:end]

        self.assertNotIn("projectPrimaryActions(p,'detail')", detail)
        card = app[app.index("function projectCard(p)"):app.index("\nfunction attentionPanel", app.index("function projectCard(p)"))]
        self.assertNotIn("projectPrimaryActions(p)", card)
        self.assertNotIn("mobileRunnerControls(p)", card)
        self.assertIn("project-worker-advanced", detail)
        self.assertIn("Worker details", detail)
        self.assertIn('data-disclosure="project-workers-${esc(p.id)}"', detail)
        self.assertIn("openDisclosures", app)
        self.assertIn("details[data-disclosure][open]", app)

    def test_dashboard_uses_consistent_action_surface_and_new_asset_revision(self):
        css = (ROOT / "public" / "enhancements.css").read_text(encoding="utf-8")
        index = (ROOT / "public" / "index.html").read_text(encoding="utf-8")

        self.assertIn("UX hierarchy v3", css)
        self.assertIn("--ux-radius:14px", css)
        self.assertIn(".project-primary-actions", css)
        self.assertIn(".overview-advanced", css)
        self.assertIn("/app.js?v=r6", index)
        self.assertIn("/enhancements.css?v=r3", index)
        self.assertIn("/design-system.css?v=r2", index)
        self.assertIn("function globalWorkerRows()", (ROOT / "public" / "app.js").read_text(encoding="utf-8"))

    def test_dashboard_javascript_parses(self):
        result = subprocess.run(
            ["node", "--check", str(ROOT / "public" / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
