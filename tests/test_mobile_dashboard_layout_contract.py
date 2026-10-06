import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "public" / "style.css"
APP = ROOT / "public" / "app.js"


class MobileDashboardLayoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLE.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")

    def test_primary_mobile_breakpoint_keeps_dashboard_single_column(self):
        self.assertIn("@media(max-width:760px)", self.css)
        self.assertIn(".project-grid{grid-template-columns:1fr", self.css)
        self.assertIn(".dashboard-grid{gap:18px}", self.css)
        self.assertIn(".detail-columns{gap:18px}", self.css)
        self.assertIn(".sidebar{display:none}", self.css)
        self.assertIn(".shell{margin-left:0}", self.css)

    def test_mobile_navigation_stays_reachable_without_horizontal_page_overflow(self):
        self.assertIn(".mobile-project-nav{display:flex;gap:8px;overflow-x:auto", self.css)
        self.assertIn("-webkit-overflow-scrolling:touch", self.css)
        self.assertIn(".mobile-project-nav{position:sticky;top:0;z-index:18}", self.css)
        self.assertIn(".mobile-nav{display:flex;position:fixed;bottom:0;left:0;right:0", self.css)
        self.assertIn("env(safe-area-inset-bottom)", self.css)
        self.assertIn("main{padding:26px 20px 70px}", self.css)
        self.assertIn('class="mobile-overview-link', self.app)
        self.assertIn('data-nav="overview"', self.app)

    def test_mobile_touch_targets_are_bounded_for_primary_controls(self):
        self.assertIn(".mobile-project-nav a{min-height:44px", self.css)
        self.assertIn(".icon-button{width:44px;height:44px}", self.css)
        self.assertIn(".mobile-runner-workers input{width:58px;min-height:44px", self.css)
        self.assertIn(".mobile-runner-button{min-width:76px;min-height:44px", self.css)
        self.assertIn(".attention-item button{width:100%;min-height:40px}", self.css)

    def test_narrow_phone_layout_reflows_runner_controls_instead_of_squeezing(self):
        self.assertIn("@media(max-width:390px)", self.css)
        self.assertIn(".mobile-runner-controls{grid-template-columns:minmax(0,1fr) auto;gap:9px}", self.css)
        self.assertIn(".mobile-runner-workers{grid-row:2;grid-column:1}", self.css)
        self.assertIn(".mobile-runner-button{grid-row:1/3;grid-column:2;min-width:82px;height:100%}", self.css)
        self.assertIn("@media(max-width:360px)", self.css)
        self.assertIn("main{padding-left:14px;padding-right:14px}", self.css)

    def test_touch_navigation_does_not_depend_on_desktop_drag_behavior(self):
        self.assertIn(".project-card[data-project-id]{touch-action:pan-y}", self.css)
        self.assertIn(".project-card{cursor:default}", self.css)
        self.assertIn(".drag-handle{display:none}", self.css)
        self.assertIn("const projectCardTarget=e.target.closest('[data-project-id]')", self.app)
        self.assertIn(
            "location.hash='project/'+encodeURIComponent(projectCardTarget.dataset.projectId)",
            self.app,
        )

    def test_reduced_motion_contract_remains_available_on_mobile(self):
        self.assertRegex(
            self.css,
            re.compile(
                r"@media\(prefers-reduced-motion:reduce\)\{\*\{animation:none!important;"
                r"transition:none!important;scroll-behavior:auto!important\}\}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
